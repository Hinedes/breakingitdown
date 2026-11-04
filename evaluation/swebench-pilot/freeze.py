"""Freeze the pilot inputs from the pinned dataset and task repo.

Run with the benchmark venv (needs pyarrow and pyyaml). It writes:

  task-inputs.jsonl      the only fields an agent may receive (20 rows)
  manifest.json          checked-in evidence, hashes, schedule, no gold data
  grading/instances.jsonl hidden grading rows (patches, tests); mode 0600
  grading/sanity.jsonl   one unscored sanity row, never one of the 20

The parquet is the source of issue text and grading patches. The task repo is
the source of image names, eval scripts, and parsers; for the 20 tasks its
patches and tests are checked against the parquet before anything is written.
"""

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

import pyarrow.parquet as pq
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol

FROZEN_IDS_SHA256 = "5aa9838900afdf9e43b9759975275b6ec5517ff942735364dc6a562c05ac9eed"
SANITY_ID = "sympy__sympy-20590"


def sha256_bytes(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_rows(parquet_path):
    actual = sha256_bytes(Path(parquet_path).read_bytes())
    if actual != protocol.DATASET_PARQUET_SHA256:
        raise SystemExit(f"parquet hash mismatch: {actual}")
    return {row["instance_id"]: row for row in pq.read_table(parquet_path).to_pylist()}


def parse_tests(value):
    return json.loads(value) if isinstance(value, str) else value


def read_exact(path):
    with open(path, newline="") as handle:
        return handle.read()


def task_metadata(repo_root, row):
    task_dir = Path(repo_root) / "tasks" / row["instance_id"]
    meta = yaml.safe_load((task_dir / "task.yaml").read_text())
    tests = json.loads((task_dir / "tests.json").read_text())
    eval_script = (task_dir / "eval.sh").read_text()
    checks = {
        "repo": (meta.get("repo"), row["repo"]),
        "base_commit": (meta.get("base_commit"), row["base_commit"]),
        "problem_statement": (
            read_exact(task_dir / "problem_statement.md").rstrip("\n"),
            row["problem_statement"].rstrip("\n"),
        ),
        "patch": (read_exact(task_dir / "gold.patch").rstrip("\n"), row["patch"].rstrip("\n")),
        "test_patch": (read_exact(task_dir / "test.patch").rstrip("\n"), row["test_patch"].rstrip("\n")),
        "FAIL_TO_PASS": (tests.get("FAIL_TO_PASS"), parse_tests(row["FAIL_TO_PASS"])),
    }
    bad = [name for name, (left, right) in checks.items() if left != right]
    if bad:
        raise SystemExit(f"task repo disagrees with frozen dataset for {row['instance_id']}: {bad}")
    return meta, tests, eval_script


def grading_row(row, meta, eval_script):
    enriched = {
        "instance_id": row["instance_id"],
        "repo": row["repo"],
        "base_commit": row["base_commit"],
        "version": str(row["version"]),
        "problem_statement": row["problem_statement"],
        "patch": row["patch"],
        "test_patch": row["test_patch"],
        "FAIL_TO_PASS": parse_tests(row["FAIL_TO_PASS"]),
        "PASS_TO_PASS": parse_tests(row["PASS_TO_PASS"]),
        "hints_text": "",
        "image": meta["image"],
        "log_parser": meta["log_parser"],
        "eval_type": meta["eval_type"],
        "eval_script": eval_script,
        "split": "test",
    }
    return enriched


def write_jsonl(path, rows, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(protocol.canonical_json(row) for row in rows) + "\n"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True)
    parser.add_argument("--task-repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--bid-source-sha256", default="")
    parser.add_argument("--bid-source-manifest", default="")
    args = parser.parse_args()

    rows = load_rows(args.parquet)
    eligible = sorted(rows)
    sampled = set(random.Random(protocol.SAMPLE_SEED).sample(eligible, protocol.INSTANCE_COUNT))
    if protocol.ids_sha256(sampled) != FROZEN_IDS_SHA256:
        raise SystemExit("sample does not match the already-frozen 20 IDs")
    if SANITY_ID in sampled or SANITY_ID not in rows:
        raise SystemExit("sanity instance must exist and stay outside the frozen 20")

    selected = [rows[instance_id] for instance_id in sorted(sampled)]
    public = [protocol.public_task(row) for row in selected]
    out = Path(args.out)
    write_jsonl(out / "task-inputs.jsonl", public)

    grading = []
    p2p_deltas = {}
    for row in selected:
        meta, tests, eval_script = task_metadata(args.task_repo, row)
        grading.append(grading_row(row, meta, eval_script))
        dataset_p2p = parse_tests(row["PASS_TO_PASS"])
        if tests.get("PASS_TO_PASS") != dataset_p2p:
            p2p_deltas[row["instance_id"]] = {
                "dataset_sha256": sha256_bytes(protocol.canonical_json(dataset_p2p)),
                "task_repo_sha256": sha256_bytes(protocol.canonical_json(tests["PASS_TO_PASS"])),
                "dataset_count": len(dataset_p2p),
                "task_repo_count": len(tests["PASS_TO_PASS"]),
                "policy": "grading uses the frozen dataset PASS_TO_PASS",
            }
    write_jsonl(out / "grading" / "instances.jsonl", grading, mode=0o600)

    sanity_row = rows[SANITY_ID]
    sanity_meta = yaml.safe_load(
        (Path(args.task_repo) / "tasks" / SANITY_ID / "task.yaml").read_text()
    )
    sanity_eval = (Path(args.task_repo) / "tasks" / SANITY_ID / "eval.sh").read_text()
    write_jsonl(
        out / "grading" / "sanity.jsonl",
        [grading_row(sanity_row, sanity_meta, sanity_eval)],
        mode=0o600,
    )

    manifest = protocol.build_manifest(
        public,
        created_at=args.created_at,
        bid_source_sha256=args.bid_source_sha256,
        bid_source_manifest=args.bid_source_manifest,
        source_policy="bid/, prompts/, pyproject.toml, evaluation/swebench-pilot/*.py",
    )
    manifest["grading"] = {
        "task_repo_commit": protocol.TASK_REPO_COMMIT,
        "enriched_rows": len(grading),
        "enriched_rows_sha256": sha256_bytes(
            "\n".join(protocol.canonical_json(row) for row in grading) + "\n"
        ),
        "sanity_instance": SANITY_ID,
        "sanity_rows_sha256": sha256_bytes(
            "\n".join(
                protocol.canonical_json(row)
                for row in [
                    grading_row(
                        sanity_row,
                        sanity_meta,
                        sanity_eval,
                    )
                ]
            )
            + "\n"
        ),
        "p2p_deltas": p2p_deltas,
        "hidden_data_location": "grading/ (mode 0600, controller only)",
    }
    manifest["controller"] = {
        "provisional_batch": protocol.PROVISIONAL_BATCH,
        "protocol_grammar": protocol.PROTOCOL_GRAMMAR,
        "max_searches_per_worker": protocol.MAX_SEARCHES,
        "task_statement": "exact public problem_statement injected into every Worker prompt",
        "policy_location": "host controller; each RUN is a fresh offline container at /testbed",
        "agent_container": "no persistent agent container; per-RUN container mounts project only, network none, cap-drop ALL, no-new-privileges",
        "patch_extraction": "container: network none, cap-drop ALL, read-only root, project mount only",
        "repo_checkout": "shallow init/fetch of the public base commit only; origin/refs/logs removed",
        "wall": "proxy writes monotonic deadline at first request; common watchdog kills runner and per-RUN/mini containers at deadline",
        "pilot": "pilot.py validates manifest, walks 60-cell order, grades uniquely, checkpoints atomically, monitors disk",
        "proxy": "apply-template + tokenize preflight, atomic reservation, fail closed on missing usage, fixed sampling/seed, JSONL audit + bounded trace",
        "mini": {
            "version": "2.4.6",
            "network": "none",
            "cap_drop": "ALL",
            "no_new_privileges": True,
            "cost_tracking": "ignore_errors",
            "sampling": "temperature 0.6, top_p 0.95, top_k 20, min_p 0, presence 0, repetition 1",
            "seed": "per issue",
            "max_tokens": protocol.REQUEST_OUTPUT_LIMIT,
        },
    }
    manifest = protocol.seal_manifest(manifest)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print("ids_sha256", manifest["sample"]["ids_sha256"])
    print("task_inputs_sha256", manifest["task_inputs_sha256"])
    print("manifest_sha256", manifest["manifest_sha256"])
    print("enriched_rows_sha256", manifest["grading"]["enriched_rows_sha256"])
    print("p2p_deltas", list(p2p_deltas))


if __name__ == "__main__":
    main()
