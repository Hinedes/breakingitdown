"""Minimal cohort controller for the frozen paired pilot.

It validates the manifest, walks the frozen issue/condition order, runs one
cell at a time through runner.py, grades the predicted patch with the official
evaluator, and checkpoints each cell atomically. It never replaces IDs,
changes the schedule on resume, or silently reruns a completed cell.

This launcher is deliberately small: no training, lessons, or cross-run memory.
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol
import runner


class PilotError(RuntimeError):
    pass


EXPECTED_IDS_SHA256 = "5aa9838900afdf9e43b9759975275b6ec5517ff942735364dc6a562c05ac9eed"


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def source_tree_hash(source_root):
    """Same deterministic tree hash used by freeze.py/SOURCE_FINGERPRINTS."""
    source_root = Path(source_root)
    rels = []
    for base in ("bid", "prompts"):
        root = source_root / base
        if not root.is_dir():
            raise PilotError(f"source root is missing {base}/")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                if name.endswith((".pyc", ".pyo")):
                    continue
                rels.append(os.path.relpath(os.path.join(dirpath, name), source_root).replace(os.sep, "/"))
    if not (source_root / "pyproject.toml").is_file():
        raise PilotError("source root is missing pyproject.toml")
    rels.append("pyproject.toml")
    pilot = source_root / "evaluation" / "swebench-pilot"
    if pilot.is_dir():
        for name in sorted(os.listdir(pilot)):
            if name.endswith(".py"):
                rels.append("evaluation/swebench-pilot/" + name)
    h = hashlib.sha256()
    for rel in sorted(set(rels)):
        data = (source_root / rel).read_bytes()
        h.update(rel.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def verify_provenance(manifest, public, grading_path, source_root):
    if manifest["sample"]["ids_sha256"] != EXPECTED_IDS_SHA256:
        raise PilotError("manifest ID hash is not the frozen 20-ID hash")
    by_id = {row["instance_id"]: row for row in manifest["instances"]}
    for instance_id, row in public.items():
        expected = by_id.get(instance_id)
        if expected is None:
            raise PilotError(f"public row not in manifest: {instance_id}")
        if row["base_commit"] != expected["base_commit"]:
            raise PilotError(f"public base commit changed for {instance_id}")
        if _sha256_bytes(row["problem_statement"].encode()) != expected["problem_statement_sha256"]:
            raise PilotError(f"public statement changed for {instance_id}")
    actual_grading = _sha256_bytes(Path(grading_path).read_bytes())
    if actual_grading != manifest["grading"]["enriched_rows_sha256"]:
        raise PilotError("hidden grading rows hash mismatch")
    actual_source = source_tree_hash(source_root)
    if actual_source != manifest["pins"]["bid_source_sha256"]:
        raise PilotError("live BID/runner/prompt source hash mismatch")
    source_manifest = Path(source_root) / manifest["pins"].get("bid_source_manifest", "SOURCE_FINGERPRINTS.sha256")
    if source_manifest.is_file():
        recorded = source_manifest.read_text(encoding="utf-8").split()[0]
        if recorded != actual_source:
            raise PilotError("SOURCE_FINGERPRINTS.sha256 does not match live source")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pilot-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def load_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if len(manifest.get("instances", [])) != protocol.INSTANCE_COUNT:
        raise PilotError("manifest does not contain 20 instances")
    ids = [row["instance_id"] for row in manifest["instances"]]
    if len(set(ids)) != len(ids):
        raise PilotError("manifest IDs are not unique")
    if protocol.ids_sha256(ids) != manifest["sample"]["ids_sha256"]:
        raise PilotError("manifest ID hash mismatch")
    sealed = protocol.seal_manifest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    if sealed["manifest_sha256"] != manifest["manifest_sha256"]:
        raise PilotError("manifest self-hash mismatch")
    if manifest["dataset"]["revision"] != protocol.DATASET_REVISION:
        raise PilotError("dataset revision mismatch")
    if manifest["dataset"]["parquet_sha256"] != protocol.DATASET_PARQUET_SHA256:
        raise PilotError("dataset parquet hash mismatch")
    return manifest


def load_public_rows(path, manifest):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != protocol.INSTANCE_COUNT:
        raise PilotError("public input file does not contain 20 rows")
    for row in rows:
        extra = set(row) - set(protocol.PUBLIC_FIELDS)
        if extra:
            raise PilotError(f"public input has non-allowlisted fields: {sorted(extra)}")
    if protocol.task_inputs_sha256(rows) != manifest["task_inputs_sha256"]:
        raise PilotError("public input hash mismatch")
    return {row["instance_id"]: row for row in rows}


def load_grading_rows(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["instance_id"]: row for row in rows}


def build_queue(manifest):
    queue = []
    for row in manifest["instances"]:
        for condition in row["order"]:
            queue.append({
                "instance_id": row["instance_id"],
                "condition": condition,
                "generation_seed": row["generation_seed"],
                "key": f"{row['instance_id']}--{condition}",
            })
    if len(queue) != protocol.INSTANCE_COUNT * len(protocol.CONDITIONS):
        raise PilotError("queue is not 60 paired cells")
    for instance_id in {cell["instance_id"] for cell in queue}:
        orders = [cell["condition"] for cell in queue if cell["instance_id"] == instance_id]
        if sorted(orders) != sorted(protocol.CONDITIONS):
            raise PilotError(f"issue {instance_id} does not have all three conditions")
    return queue


def _disk_free_gb(path):
    return shutil.disk_usage(path).free / (1024 ** 3)


def _read_results(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    keys = ("resolved_instances", "unresolved_instances", "infra_failure_instances",
            "error_instances", "empty_patch_instances", "completed_instances")
    return {key: data.get(key) for key in keys}


def _proc_starttime(pid):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return None
    # Field 22 is starttime; fields after comm are split from the last ')'.
    fields = stat[stat.rfind(")") + 2:].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _stop_owned_server(pid_file, identity_file=None, binary=None, model=None,
                       host="127.0.0.1", port=8081):
    """Fail-closed stop of an owned llama-server; never kill an unverified PID."""
    if not pid_file or not Path(pid_file).is_file():
        return {"stopped": False, "reason": "no pid file"}
    if not binary or not model:
        return {"stopped": False, "reason": "expected binary/model not configured"}
    try:
        pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
    except ValueError:
        return {"stopped": False, "reason": "invalid pid file"}
    if pid <= 0 or not Path(f"/proc/{pid}").exists():
        return {"stopped": False, "reason": "process not running"}
    identity = None
    if identity_file and Path(identity_file).is_file():
        try:
            identity = json.loads(Path(identity_file).read_text(encoding="utf-8"))
        except ValueError:
            return {"stopped": False, "reason": "invalid start identity file"}
    if not identity or int(identity.get("pid", -1)) != pid:
        return {"stopped": False, "reason": "no matching start identity"}
    before = _proc_starttime(pid)
    if before is None or int(identity.get("starttime_ticks", -1)) != before:
        return {"stopped": False, "reason": "process start identity changed"}
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
    except OSError:
        return {"stopped": False, "reason": "cmdline unavailable"}
    if not argv or argv[0] != binary:
        return {"stopped": False, "reason": "executable mismatch"}
    for flag in ("--model", "--host", "--port"):
        if argv.count(flag) != 1 or argv.index(flag) + 1 == len(argv):
            return {"stopped": False, "reason": f"{flag} missing or ambiguous"}
    if (argv[argv.index("--model") + 1] != model
            or argv[argv.index("--host") + 1] != host
            or argv[argv.index("--port") + 1] != str(port)):
        return {"stopped": False, "reason": "model/host/port mismatch"}
    state = None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        state = stat[stat.rfind(")") + 2:].split()[0]
    except OSError:
        pass
    if state in {None, "Z", "X"}:
        return {"stopped": False, "reason": "process not live"}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"stopped": False, "reason": "process disappeared before signal"}
    except PermissionError:
        return {"stopped": False, "reason": "permission denied"}
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists() or _proc_starttime(pid) != before:
            return {"stopped": True, "pid": pid, "reason": "SIGTERM stopped process"}
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
            if stat[stat.rfind(")") + 2:].split()[0] in {"Z", "X"}:
                return {"stopped": True, "pid": pid, "reason": "SIGTERM stopped process"}
        except OSError:
            return {"stopped": True, "pid": pid, "reason": "SIGTERM stopped process"}
        time.sleep(0.1)
    return {"stopped": False, "pid": pid, "reason": "did not stop within 15s"}


def run_one(args, manifest, public, grading, cell, queue_index, total):
    instance_id = cell["instance_id"]
    condition = cell["condition"]
    row = public[instance_id]
    hidden = grading[instance_id]
    cell_dir = Path(args.output) / cell["key"]
    checkpoint = cell_dir / "complete.json"
    if checkpoint.exists():
        return {"key": cell["key"], "status": "skipped_complete"}
    cell_dir.mkdir(parents=True, exist_ok=True)
    free = _disk_free_gb(args.output)
    if free < args.min_free_gb:
        raise PilotError(f"stopping before {cell['key']}: only {free:.1f} GiB free")
    source_sha = manifest["pins"]["bid_source_sha256"]
    started = time.monotonic()
    repo_source = None
    if args.repo_cache:
        candidate = Path(args.repo_cache) / row["repo"].replace("/", "__")
        if candidate.exists():
            repo_source = str(candidate)
    if repo_source is None:
        repo_source = f"https://github.com/{row['repo']}.git"
    if condition == "mini":
        try:
            result = runner.run_mini_agent_cell(
                cell_dir, row, str(repo_source), hidden["image"], args.model_upstream,
                args.model, cell["generation_seed"], args.python, args.mini_python,
                timeout=protocol.WALL_BUDGET_SECONDS,
            )
        except Exception as exc:
            result = {"status": "error", "error": str(exc), "prediction": None}
    else:
        try:
            result = runner.run_agent_cell(
                condition, cell_dir, row, str(repo_source), hidden["image"],
                args.model_upstream, args.model, cell["generation_seed"],
                args.python, timeout=protocol.WALL_BUDGET_SECONDS,
            )
        except Exception as exc:
            result = {"status": "error", "error": str(exc), "prediction": None}
    prediction = cell_dir / "controller/prediction.jsonl"
    if not prediction.exists():
        outcome = {
            "key": cell["key"], "queue_index": queue_index, "total": total,
            "instance_id": instance_id, "condition": condition,
            "generation_seed": cell["generation_seed"],
            "source_sha256": source_sha, "manifest_sha256": manifest["manifest_sha256"],
            "cell_status": result.get("status"), "cell_error": result.get("error", ""),
            "grade_returncode": None, "grade_timed_out": False, "grade": {},
            "disk_free_gb": round(_disk_free_gb(args.output), 2),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "completed": False,
        }
        atomic_write(checkpoint, json.dumps(outcome, sort_keys=True) + "\n")
        with open(Path(args.output) / "outcomes.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(outcome, sort_keys=True) + "\n")
        return outcome
    run_id = protocol.new_grade_run_id(condition, source_sha, attempt=1)
    grade = protocol.run_bounded(
        protocol.grade_command(args.python, prediction, run_id, [instance_id],
                               dataset=str(Path(args.grading_rows))),
        args.grade_timeout, cwd=args.controller_root, env=os.environ,
    )
    (cell_dir / "grade.log").write_text(grade.get("output") or "", encoding="utf-8")
    results_path = Path(args.controller_root) / "logs" / "evaluation" / run_id / "results.json"
    grade_result = _read_results(results_path) if results_path.exists() else {
        "resolved_instances": None, "unresolved_instances": None,
        "infra_failure_instances": None, "error_instances": None,
        "empty_patch_instances": None, "completed_instances": None,
    }
    outcome = {
        "key": cell["key"],
        "queue_index": queue_index,
        "total": total,
        "instance_id": instance_id,
        "condition": condition,
        "generation_seed": cell["generation_seed"],
        "source_sha256": source_sha,
        "manifest_sha256": manifest["manifest_sha256"],
        "run_id": run_id,
        "prediction": str(prediction),
        "cell_status": result.get("status"),
        "cell_error": result.get("error", ""),
        "grade_returncode": grade.get("returncode"),
        "grade_timed_out": grade.get("timed_out"),
        "grade": grade_result,
        "disk_free_gb": round(_disk_free_gb(args.output), 2),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "completed": grade.get("returncode") == 0 and results_path.exists(),
    }
    atomic_write(checkpoint, json.dumps(outcome, sort_keys=True) + "\n")
    with open(Path(args.output) / "outcomes.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(outcome, sort_keys=True) + "\n")
        handle.flush()
    return outcome


def summary_rows(output, queue):
    """Rebuild the 60-row summary from actual atomic checkpoints, never stubs."""
    rows = []
    for cell in queue:
        path = Path(output) / cell["key"] / "complete.json"
        if path.exists():
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            rows.append({
                "key": cell["key"],
                "instance_id": cell["instance_id"],
                "condition": cell["condition"],
                "completed": False,
                "checkpoint_missing": True,
            })
    return rows


def write_csv(rows, path):
    path = Path(path)
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row})
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, dict) else value
                             for key, value in row.items()})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--public", default="frozen/task-inputs.jsonl")
    parser.add_argument("--grading-rows", default="frozen/grading/instances.jsonl")
    parser.add_argument("--output", default="runs")
    parser.add_argument("--controller-root", default=".")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--repo-cache", required=False)
    parser.add_argument("--model-upstream", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--model", default="Qwen3-4B-Instruct-2507-Q8_0")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--mini-python", default="mini-venv/bin/python")
    parser.add_argument("--grade-timeout", type=int, default=1800)
    parser.add_argument("--min-free-gb", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--instance", default=None)
    parser.add_argument("--condition", choices=protocol.CONDITIONS, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--server-pid-file", default=None)
    parser.add_argument("--server-identity-file", default=None)
    parser.add_argument("--server-binary", default=None)
    parser.add_argument("--server-model", default=None)
    parser.add_argument("--server-host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=8081)
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    public = load_public_rows(args.public, manifest)
    grading = load_grading_rows(args.grading_rows)
    verify_provenance(manifest, public, args.grading_rows, args.source_root)
    queue = build_queue(manifest)
    if args.instance:
        queue = [cell for cell in queue if cell["instance_id"] == args.instance]
    if args.condition:
        queue = [cell for cell in queue if cell["condition"] == args.condition]
    if args.limit is not None:
        if args.limit <= 0:
            raise SystemExit("--limit must be positive")
        queue = queue[:args.limit]
    if args.dry_run:
        print(json.dumps({"queue": len(queue), "cells": queue[:5]}, indent=2))
        return 0
    if not args.execute:
        raise SystemExit("refusing to run without --execute; use --dry-run to inspect")
    Path(args.output).mkdir(parents=True, exist_ok=True)
    outcomes = []
    try:
        for index, cell in enumerate(queue, 1):
            print(f"[{index}/{len(queue)}] {cell['key']}", flush=True)
            outcome = run_one(args, manifest, public, grading, cell, index, len(queue))
            outcomes.append(outcome)
            if outcome.get("status") != "skipped_complete":
                print(json.dumps(outcome.get("grade"), sort_keys=True), flush=True)
    finally:
        stopped = _stop_owned_server(
            args.server_pid_file, args.server_identity_file, args.server_binary,
            args.server_model, args.server_host, args.server_port,
        )
        write_csv(summary_rows(args.output, queue), Path(args.output) / "outcomes.csv")
        (Path(args.output) / "server-stopped.json").write_text(
            json.dumps(stopped, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
