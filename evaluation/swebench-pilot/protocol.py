"""Small, testable invariants for the paired SWE-bench pilot."""

import hashlib
import itertools
import json
import os
import random
import signal
import subprocess
import time
import uuid


CONDITIONS = ("bid", "worker", "mini")
SAMPLE_SEED = 20261102
INSTANCE_COUNT = 20
WALL_BUDGET_SECONDS = 600
TOKEN_BUDGET = 180_000
REQUEST_OUTPUT_LIMIT = 4096
CONTEXT_TOKENS = 65536
PROVISIONAL_BATCH = 1
MAX_SEARCHES = 0
PROTOCOL_GRAMMAR = False

DATASET_REPO = "princeton-nlp/SWE-bench_Verified"
DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
DATASET_PARQUET_SHA256 = "a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd"
EVALUATOR_COMMIT = "02e7a74ffd0b707aab73d203fe87bdc7c76afc8e"
TASK_REPO_COMMIT = "3d07b464b7b311a0cbfb5ed5b2d8a3b96f84a33d"
MINI_COMMIT = "04d809ceab9df28f9adaed044884180159172930"
MODEL_SHA256 = "260b5b5b6ad73e44df81a43ea1f5c11c37007b6bac18eb3cd2016e8667c19662"
LLAMA_CPP_COMMIT = "8642541b255bfb5dc39cd351a178be09013a55d6"

# BID writes these into the candidate checkout; none may reach a prediction.
CONTROL_PATHS = (
    ".bid/",
    ".bid_backup/",
    "docs/manager.md",
    "docs/worker.md",
    "docs/task.md",
    "docs/todo.md",
    "docs/project-status.md",
    "docs/decisions.md",
)
PUBLIC_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")
EMPTY_PATCH_PATH = ".bid-empty-patch"


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ids_sha256(instance_ids):
    """Stable digest of the frozen ID set: sorted, newline joined, trailing newline."""
    return _sha256("\n".join(sorted(instance_ids)) + "\n")


def task_inputs_sha256(rows):
    """Digest of exactly what an agent may see, in manifest order."""
    return _sha256("\n".join(canonical_json(public_task(row)) for row in rows) + "\n")


def freeze_instances(rows, seed=SAMPLE_SEED, count=INSTANCE_COUNT):
    """Sample from IDs only, then retain only agent-visible task fields."""
    eligible = sorted(
        (row["instance_id"], row["repo"], row["base_commit"], row["problem_statement"])
        for row in rows
    )
    ids = [row[0] for row in eligible]
    if len(ids) != len(set(ids)) or len(ids) < count:
        raise ValueError("dataset IDs must be unique and contain the requested sample size")
    selected = set(random.Random(seed).sample(ids, count))
    return [
        {"instance_id": instance_id, "repo": repo, "base_commit": base_commit,
         "problem_statement": statement}
        for instance_id, repo, base_commit, statement in eligible
        if instance_id in selected
    ]


def paired_schedule(instances, seed=SAMPLE_SEED):
    """Counterbalance condition order over issue blocks; every issue gets all arms."""
    permutations = list(itertools.permutations(CONDITIONS))
    rng = random.Random(seed ^ 0x5EED)
    rng.shuffle(permutations)
    return {
        row["instance_id"]: list(permutations[index % len(permutations)])
        for index, row in enumerate(instances)
    }


def public_task(row):
    """Never pass dataset reference patches, tests, hints, or metadata to agents."""
    return {key: row[key] for key in ("instance_id", "repo", "base_commit", "problem_statement")}


def generation_seed(instance_id, seed=SAMPLE_SEED):
    digest = hashlib.sha256(f"{seed}:{instance_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def build_manifest(rows, seed=SAMPLE_SEED, created_at=None, **pins):
    """Checked-in evidence: IDs and fingerprints, never gold patches or hints."""
    if len(rows) != INSTANCE_COUNT or len({r["instance_id"] for r in rows}) != INSTANCE_COUNT:
        raise ValueError("manifest requires the frozen, unique instance set")
    ordered = sorted(rows, key=lambda row: row["instance_id"])
    schedule = paired_schedule(ordered, seed)
    instances = [
        {
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "problem_statement_sha256": _sha256(row["problem_statement"]),
            "order": schedule[row["instance_id"]],
            "generation_seed": generation_seed(row["instance_id"], seed),
        }
        for row in ordered
    ]
    manifest = {
        "schema": 1,
        "created_at": created_at,
        "dataset": {
            "repo": DATASET_REPO,
            "revision": DATASET_REVISION,
            "split": "test",
            "pool_size": 500,
            "parquet_sha256": DATASET_PARQUET_SHA256,
        },
        "sample": {
            "seed": seed,
            "count": INSTANCE_COUNT,
            "algorithm": "sorted IDs; random.Random(seed).sample(ids, 20); output sorted by ID",
            "ids_sha256": ids_sha256([row["instance_id"] for row in ordered]),
        },
        "conditions": list(CONDITIONS),
        "budgets": {
            "wall_seconds": WALL_BUDGET_SECONDS,
            "prompt_plus_completion_tokens": TOKEN_BUDGET,
            "max_generated_tokens_per_request": REQUEST_OUTPUT_LIMIT,
            "context_tokens": CONTEXT_TOKENS,
            "provisional_batch": PROVISIONAL_BATCH,
            "max_searches_per_worker": MAX_SEARCHES,
            "protocol_grammar": PROTOCOL_GRAMMAR,
        },
        "pins": {
            "evaluator_swebench": EVALUATOR_COMMIT,
            "task_repo": TASK_REPO_COMMIT,
            "mini_swe_agent": MINI_COMMIT,
            "model_sha256": MODEL_SHA256,
            "llama_cpp": LLAMA_CPP_COMMIT,
            **pins,
        },
        "task_inputs_sha256": task_inputs_sha256(ordered),
        "instances": instances,
    }
    manifest["manifest_sha256"] = _sha256(canonical_json(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    ))
    return manifest


def seal_manifest(manifest):
    """Recompute the self-hash after adding evidence fields."""
    sealed = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    sealed["manifest_sha256"] = _sha256(canonical_json(sealed))
    return sealed


def _diff_paths(chunk):
    first = chunk.splitlines()[0]
    if not first.startswith("diff --git "):
        return None
    parts = first.split()
    if len(parts) < 4:
        return None
    return parts[2][2:], parts[3][2:]


def filter_patch(patch, excluded=CONTROL_PATHS):
    """Drop controller/BID files from a candidate diff; keep project files."""
    chunks = []
    current = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                chunks.append("".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        chunks.append("".join(current))

    kept = []
    for chunk in chunks:
        paths = _diff_paths(chunk)
        if paths is None:
            raise ValueError("prediction diff has a malformed chunk")
        if any(path == excluded_path.rstrip("/") or path.startswith(excluded_path)
               for path in paths for excluded_path in excluded):
            continue
        kept.append(chunk)
    return "".join(kept)


def empty_patch_sentinel(path=EMPTY_PATCH_PATH):
    """A valid, test-neutral diff for a run that produced no candidate changes.

    The official harness silently skips an empty model_patch. A sentinel keeps
    the cell in the denominator and still grades as unresolved.
    """
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "index 0000000..e69de29\n"
    )


def prediction(instance_id, model_name_or_path, patch, sentinel=EMPTY_PATCH_PATH):
    """Official prediction row; sentinel only when the arm changed nothing."""
    cleaned = filter_patch(patch)
    return {
        "instance_id": instance_id,
        "model_name_or_path": model_name_or_path,
        "model_patch": cleaned or empty_patch_sentinel(sentinel),
    }


def new_grade_run_id(condition, source_sha, attempt=1):
    if condition not in CONDITIONS or len(source_sha) < 8 or attempt < 1:
        raise ValueError("invalid grading identity")
    return f"verified20-{condition}-{source_sha[:12]}-a{attempt}-{uuid.uuid4().hex[:12]}"


class Budget:
    def __init__(self, seconds=WALL_BUDGET_SECONDS, tokens=TOKEN_BUDGET,
                 request_output_limit=REQUEST_OUTPUT_LIMIT, clock=time.monotonic):
        self.seconds = seconds
        self.tokens = tokens
        self.request_output_limit = request_output_limit
        self.clock = clock
        self.started = clock()
        self.used_tokens = 0
        self.cached_prompt_tokens = 0

    def charge(self, prompt_tokens, completion_tokens, cached_prompt_tokens=0):
        if min(prompt_tokens, completion_tokens, cached_prompt_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if cached_prompt_tokens > prompt_tokens:
            raise ValueError("cached prompt tokens exceed prompt tokens")
        if completion_tokens > self.request_output_limit:
            raise ValueError("request exceeded output-token cap")
        self.check_time()
        total = prompt_tokens + completion_tokens
        if self.used_tokens + total > self.tokens:
            raise ValueError("paired-condition token budget exceeded")
        self.used_tokens += total
        self.cached_prompt_tokens += cached_prompt_tokens

    def check_time(self):
        if self.clock() - self.started > self.seconds:
            raise TimeoutError("paired-condition wall budget exceeded")


def grade_command(python, predictions, run_id, instance_ids, dataset=DATASET_REPO):
    """Official SWE-bench v5 module CLI; caller must use a fresh run_id per attempt.

    ``dataset`` may be a local JSONL/parquet/name. The v5 harness reads
    image/eval_script/log_parser/eval_type from the instance rows, so the pilot
    passes the controller-built enriched JSONL rather than the bare HF parquet.
    """
    if not run_id or not instance_ids or len(instance_ids) != len(set(instance_ids)):
        raise ValueError("grading requires a unique run ID and unique instance IDs")
    if not dataset:
        raise ValueError("grading requires a dataset or local JSONL path")
    return [
        python, "-m", "swebench.harness.run_evaluation",
        "--dataset_name", str(dataset),
        "--predictions_path", str(predictions), "--run_id", run_id,
        "--instance_ids", *instance_ids, "--max_workers", "1",
    ]


def run_bounded(command, timeout, *, cwd=None, env=None):
    """Run controller work with an external process-group deadline."""
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        output, _ = process.communicate()
        return {"returncode": process.returncode, "timed_out": True, "output": output}
    return {"returncode": process.returncode, "timed_out": False, "output": output}
