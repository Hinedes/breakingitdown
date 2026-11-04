"""Small resumable driver for the frozen local-model comparison."""

import argparse
import hashlib
import json
import os
import pathlib
import random
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
import uuid

import oracles
from bid import adapter, harness, vc
from sandbox import run_command


ROOT = pathlib.Path(__file__).resolve().parent
TASK_ROOT = ROOT / "tasks"
REMOTE_ROOT = pathlib.Path(os.environ.get("BID_STUDY_ROOT", ROOT))
SOURCE_EPOCH = os.environ.get("BID_SOURCE_EPOCH", "epoch-9")
RUN_ROOT = REMOTE_ROOT / f"runs-{SOURCE_EPOCH}"
DB_PATH = REMOTE_ROOT / f"queue-{SOURCE_EPOCH}.sqlite3"
PYTHON_HOME = os.environ.get("BID_STUDY_PYTHON", "/home/iclab/bid-llama-runtime/tools/python312/python")
SEED = 20261101
CUTOFF_UTC = 1793577600
TOKEN_CAP = int(os.environ.get("BID_STUDY_TOKEN_CAP", "100000"))
WALL_CAP = int(os.environ.get("BID_STUDY_WALL_CAP", "300"))
OUTPUT_CAP = 8192
MODEL_PATH = os.environ.get("BID_STUDY_MODEL_PATH", "/home/iclab/bid-models/Llama-3.2-1B-Instruct-f16.gguf")
MODEL_SHA256 = os.environ.get("BID_STUDY_MODEL_SHA256", "1f33ad43d2b85b908ff06fe7002b69806a57359b9b2617ca27d7bdea428ae146")
MODEL_ALIAS = os.environ.get("BID_STUDY_MODEL_ALIAS", "Llama-3.2-1B-Instruct-f16")
KV_CACHE_TYPE = os.environ.get("BID_STUDY_KV_CACHE_TYPE", "f16")
CONSTRAINED = os.environ.get("BID_STUDY_CONSTRAINED") == "1"
SANDBOX_RUN = os.environ.get("BID_STUDY_SANDBOX_RUN") == "1"
SERVER_CONFIG = pathlib.Path(os.environ.get("BID_SERVER_CONFIG", "/home/iclab/bid-llama-runtime/config/launch.json"))
SERVER_PID = pathlib.Path(os.environ.get("BID_SERVER_PID", "/home/iclab/bid-llama-runtime/server.pid"))
CONDITIONS = {
    "B128-B4": ("bid", 131072, 4),
    "B128-B1": ("bid", 131072, 1),
    "B64-B4": ("bid", 65536, 4),
    "D128": ("direct", 131072, 1),
    "D64": ("direct", 65536, 1),
}
_DEFAULT_PROMPT_LOADER = harness.load_prompt


def configure_execution(config):
    if SANDBOX_RUN:
        temp_root = REMOTE_ROOT / "worker-tmp"
        temp_root.mkdir(parents=True, exist_ok=True)

        def isolated(argv, *, cwd, timeout):
            return run_command(cwd, argv, PYTHON_HOME, temp_root, timeout=timeout)

        config["run_executor"] = isolated
        config["run_timeout"] = 30
    if CONSTRAINED:
        from protocol import RECONCILE_POLICY, REVIEW_POLICY, WORKER_POLICY

        def prompt(name):
            return WORKER_POLICY if name == "worker" else _DEFAULT_PROMPT_LOADER(name)

        harness.load_prompt = prompt
        adapter.RECONCILIATION_SYSTEM = RECONCILE_POLICY
        adapter.TASK_REVIEWER_SYSTEM = REVIEW_POLICY
    return config


class BudgetStop(RuntimeError):
    pass


class TracedBackend:
    def __init__(self, backend, trace_path, deadline, token_cap=TOKEN_CAP):
        self.backend = backend
        self.seed = getattr(backend, "seed", None)
        self.trace_path = pathlib.Path(trace_path)
        self.deadline = deadline
        self.token_cap = token_cap
        self.tokens = 0
        self.requests = 0
        self.attempts = 0
        self.roles = {}
        self.missing_usage = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cached_prompt_tokens = 0
        self.stop_reason = None
        self.started = time.monotonic()

    @staticmethod
    def _role(messages):
        system = " ".join(m.get("content", "") for m in messages if m.get("role") == "system").lower()
        if "review assignment" in system or "task reviewer" in system:
            return "reviewer"
        if "project reconciliation" in system:
            return "manager_reconcile"
        if "manager" in system:
            return "manager"
        return "worker"

    def run(self, messages, tools, max_tokens=None, timeout=None):
        remaining_wall = self.deadline - time.monotonic()
        if remaining_wall <= 0:
            self.stop_reason = "wall_cap"
            raise BudgetStop("wall_cap")
        if self.tokens >= self.token_cap:
            self.stop_reason = "token_cap"
            raise BudgetStop("token_cap")
        token_url = os.environ.get("BID_STUDY_INPUT_TOKENS", "http://127.0.0.1:8081/v1/chat/completions/input_tokens")
        payload = {
            "model": MODEL_ALIAS, "messages": messages,
            "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
            "presence_penalty": 0.0, "repetition_penalty": 1.0,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        request = urllib.request.Request(
            token_url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=min(10, remaining_wall)) as response:
            input_tokens = int(json.load(response)["input_tokens"])
        allowance = min(max_tokens or OUTPUT_CAP, OUTPUT_CAP, self.token_cap - self.tokens - input_tokens)
        if allowance <= 0:
            self.stop_reason = "token_cap_prompt"
            raise BudgetStop("token_cap_prompt")
        role = self._role(messages)
        started = time.monotonic()
        self.attempts += 1
        record = {
            "request": self.attempts,
            "role": role,
            "seed": self.seed,
            "messages": messages,
            "tools": tools,
            "max_tokens": allowance,
            "input_tokens_preflight": input_tokens,
        }
        options = {}
        if CONSTRAINED:
            from protocol import grammar_for
            options["grammar"] = grammar_for(messages)
            record["grammar_sha256"] = hashlib.sha256(options["grammar"].encode()).hexdigest()
        try:
            response = self.backend.run(
                messages, tools, max_tokens=allowance,
                timeout=max(1, min(timeout or remaining_wall, remaining_wall)),
                **options,
            )
            usage = response.get("usage") or {}
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            if total is None and prompt is not None and completion is not None:
                total = prompt + completion
            if total is None and completion is not None:
                total = input_tokens + completion
            if total is None:
                self.missing_usage += 1
            else:
                self.tokens += int(total)
            if prompt is not None:
                self.prompt_tokens += int(prompt)
            if completion is not None:
                self.completion_tokens += int(completion)
            details = usage.get("prompt_tokens_details") or {}
            self.cached_prompt_tokens += int(details.get("cached_tokens", 0))
            self.requests += 1
            self.roles[role] = self.roles.get(role, 0) + 1
            record.update({
                "response": response,
                "usage": usage,
                "input_tokens_preflight": input_tokens,
                "wall_s": round(time.monotonic() - started, 3),
            })
            return response
        except Exception as exc:
            record.update({"error": type(exc).__name__, "wall_s": round(time.monotonic() - started, 3)})
            raise
        finally:
            with self.trace_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def digest(path):
    if pathlib.Path(path).is_symlink():
        raise ValueError("refusing to hash symlink target")
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = pathlib.Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def source_manifest_path():
    configured = os.environ.get("BID_SOURCE_MANIFEST")
    if configured:
        path = pathlib.Path(configured)
        if path.is_file():
            return path
        raise FileNotFoundError(f"source manifest not found: {path}")
    for parent in ROOT.parents:
        path = parent / "SOURCE_MANIFEST.json"
        if path.is_file():
            return path
    raise FileNotFoundError("immutable SOURCE_MANIFEST.json not found above study source")


def tree_hash(root):
    values = []
    for path, kind in workspace_entries(root):
        if kind == "symlink":
            values.append((path.relative_to(root).as_posix(), "symlink:" + os.readlink(path)))
        elif kind == "file":
            values.append((path.relative_to(root).as_posix(), digest(path)))
        else:
            values.append((path.relative_to(root).as_posix(), "special file"))
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def workspace_entries(root):
    entries = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in {".bid", "__pycache__", ".pytest_cache"}]
        links = [name for name in dirs if pathlib.Path(directory, name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in files + links:
            path = pathlib.Path(directory, name)
            kind = "symlink" if path.is_symlink() else "file" if stat.S_ISREG(path.lstat().st_mode) else "special"
            entries.append((path, kind))
    return sorted(entries)


def _connect():
    REMOTE_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS queue (run_id TEXT PRIMARY KEY, task TEXT, repeat INTEGER, condition TEXT, paired_with TEXT, status TEXT, run_dir TEXT, result TEXT)")
    return db


def _schedule(db):
    if db.execute("SELECT count(*) FROM queue").fetchone()[0]:
        return
    pairs = [("B128-B4", "D128"), ("B128-B1", "D128"), ("B64-B4", "D64")]
    rows = []
    rng = random.Random(SEED)
    for repeat in range(1, 4):
        for task in oracles.TASKS:
            for bid_condition, direct_condition in pairs:
                order = [(bid_condition, direct_condition), (direct_condition, bid_condition)]
                rng.shuffle(order)
                for condition, pair in order:
                    if condition == direct_condition and any(
                        row[1] == task and row[2] == repeat and row[3] == condition for row in rows
                    ):
                        continue
                    rows.append((uuid.uuid4().hex, task, repeat, condition, pair, "pending", "", ""))
    # Preserve shuffled paired order within each context block; avoid needless reloads.
    rows.sort(key=lambda row: -CONDITIONS[row[3]][1])
    db.executemany("INSERT INTO queue VALUES (?,?,?,?,?,?,?,?)", rows)
    db.commit()


def _ensure_baselines():
    baseline_path = REMOTE_ROOT / "baselines.json"
    if baseline_path.exists():
        return
    results = {}
    temp = REMOTE_ROOT / "oracle-tmp"
    temp.mkdir(parents=True, exist_ok=True)
    for task in oracles.TASKS:
        with tempfile_workspace(task) as workspace:
            results[task] = {
                **oracles.evaluate(task, workspace, PYTHON_HOME, temp),
                "seed_workspace_sha256": tree_hash(workspace),
            }
    baseline_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


class tempfile_workspace:
    def __init__(self, task):
        self.task = task
        self.path = None

    def __enter__(self):
        import tempfile
        self.path = pathlib.Path(tempfile.mkdtemp(prefix=f"seed-{self.task}-", dir=REMOTE_ROOT))
        _copy_seed(self.task, self.path)
        return self.path

    def __exit__(self, *_):
        shutil.rmtree(self.path, ignore_errors=True)


def _copy_seed(task, workspace):
    seed = TASK_ROOT / task
    for source in seed.rglob("*"):
        if source.is_file() and source.name != "task.md":
            target = pathlib.Path(workspace) / source.relative_to(seed)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _task_text(task):
    return (TASK_ROOT / task / "task.md").read_text(encoding="utf-8").strip()


def _direct_init(task, config):
    workspace = pathlib.Path(config["workspace"])
    _copy_seed(task, workspace)
    harness.ensure_workspace(str(workspace))
    task_text = _task_text(task)
    description = " ".join(task_text.split())
    (workspace / "docs/task.md").write_text(f"# Task\n\n{task_text}\n", encoding="utf-8")
    (workspace / "docs/todo.md").write_text(f"# TODO\n\n- [ ] T1 — {description}\n", encoding="utf-8")
    (workspace / "docs/project-status.md").write_text("# Project Status\n\nInitialized.\n", encoding="utf-8")
    (workspace / "docs/decisions.md").write_text("# Decisions\n\n", encoding="utf-8")
    store = vc.VersionControl(str(workspace))
    store.init()
    return {"status": "success", "state": store.get_current()}


def _server_context():
    url = os.environ.get("BID_STUDY_PROPS", "http://127.0.0.1:8081/props")
    with urllib.request.urlopen(url, timeout=5) as response:
        props = json.load(response)
    return int(props.get("default_generation_settings", {}).get("n_ctx", 0))


def _server_matches(context):
    url = os.environ.get("BID_STUDY_PROPS", "http://127.0.0.1:8081/props")
    with urllib.request.urlopen(url, timeout=5) as response:
        props = json.load(response)
    aliases = props.get("model_alias", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    return (
        MODEL_ALIAS in aliases
        and int(props.get("default_generation_settings", {}).get("n_ctx", 0)) == context
        and int(props.get("total_slots", 0)) == 1
    )


def _process_state(pid):
    try:
        status = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return None
    return status[status.rfind(")") + 2]


def owned_server_command(argv, binary):
    models = {
        MODEL_PATH,
        "/home/iclab/bid-models/Llama-3.2-1B-Instruct-f16.gguf",
        "/home/iclab/bid-models/Llama-3.2-3B-Instruct-Q8_0.gguf",
    }
    if not argv or argv[0] != binary:
        return False
    for flag in ("--model", "--host", "--port"):
        if argv.count(flag) != 1 or argv.index(flag) + 1 == len(argv):
            return False
    return (
        argv[argv.index("--model") + 1] in models
        and argv[argv.index("--host") + 1] == "127.0.0.1"
        and argv[argv.index("--port") + 1] == "8081"
    )


def _set_server_context(context):
    try:
        if _server_matches(context):
            return
    except OSError:
        pass
    launch = json.loads(SERVER_CONFIG.read_text(encoding="utf-8"))
    pid = int(SERVER_PID.read_text(encoding="ascii").strip()) if SERVER_PID.exists() else None
    if pid is not None and _process_state(pid) not in {None, "Z", "X"}:
        argv = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
        if not owned_server_command(argv, launch["command"][0]):
            raise RuntimeError("owned server PID did not match the pinned llama-server/model; refusing restart")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _process_state(pid) in {None, "Z", "X"}:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("owned llama-server did not stop within 15 seconds")
    memory = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    if memory.returncode or not memory.stdout.strip() or int(memory.stdout.splitlines()[0]) < 8000:
        raise RuntimeError("GPU has less than 8 GiB free after stopping our server; refusing to load")
    command = list(launch["command"])
    settings = {
        "--ctx-size": str(context), "--kv-unified-per-slot": str(context),
        "--model": MODEL_PATH, "--alias": MODEL_ALIAS,
        "--cache-type-k": KV_CACHE_TYPE, "--cache-type-v": KV_CACHE_TYPE,
    }
    for flag, value in settings.items():
        if flag in command:
            command[command.index(flag) + 1] = value
        else:
            command.extend((flag, value))
    env = {
        "PATH": "/usr/local/cuda-12.8/bin:/usr/bin:/bin",
        "HOME": "/home/iclab",
        "CUDA_HOME": "/usr/local/cuda-12.8",
        "LD_LIBRARY_PATH": "/home/iclab/bid-llama-runtime/src/llama.cpp/build/bin:/usr/local/cuda-12.8/lib64:/usr/lib/wsl/lib:/usr/lib/x86_64-linux-gnu",
        "BID_WORKER_CONTINUITY": "reset",
        "BID_REPO_CONTEXT": "0",
    }
    log_path = pathlib.Path("/home/iclab/bid-llama-runtime/logs/llama-server-seeded.log")
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command, cwd=launch["working_directory"], env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    SERVER_PID.write_text(f"{process.pid}\n", encoding="ascii")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("restarted llama-server exited before readiness")
        try:
            if _server_matches(context):
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("restarted llama-server did not report the requested effective context")


def _runtime_metadata(context):
    with urllib.request.urlopen(os.environ.get("BID_STUDY_PROPS", "http://127.0.0.1:8081/props"), timeout=5) as response:
        props = json.load(response)
    with urllib.request.urlopen(os.environ.get("BID_STUDY_SLOTS", "http://127.0.0.1:8081/slots"), timeout=5) as response:
        slots = json.load(response)
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=10,
    )
    return {
        "model_alias": props.get("model_alias"), "model_ftype": props.get("model_ftype"),
        "llama_build": props.get("build_info"), "effective_context": context,
        "slots": props.get("total_slots"), "slot_contexts": [slot.get("n_ctx") for slot in slots],
        "kv_type_k": KV_CACHE_TYPE, "kv_type_v": KV_CACHE_TYPE,
        "gpu": gpu.stdout.strip() if gpu.returncode == 0 else "unavailable",
    }


def _run_one(db, row):
    run_id, task, repeat, condition, paired_with, _, _, _ = row
    runner, context, batch = CONDITIONS[condition]
    _set_server_context(context)
    actual_model = digest(MODEL_PATH)
    if actual_model != MODEL_SHA256:
        raise RuntimeError(f"model hash mismatch: {actual_model}")
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "workspace"
    workspace.mkdir()
    _copy_seed(task, workspace)
    trace_path = run_dir / "trace.jsonl"
    outcome_path = run_dir / "outcome.json"
    config = harness.get_config()
    config.update({
        "workspace": str(workspace), "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        "model_name": MODEL_ALIAS, "max_tokens": OUTPUT_CAP,
        "request_timeout": WALL_CAP, "worker_timeout": WALL_CAP,
        "inactivity_timeout": WALL_CAP, "provisional_batch": batch,
        "allow_host_run": False, "max_searches_per_worker": 0,
        "seed": SEED + repeat,
    })
    configure_execution(config)
    os.environ.pop("BID_ALLOW_HOST_RUN", None)
    os.environ["BID_REPO_CONTEXT"] = "0"
    os.environ["BID_WORKER_CONTINUITY"] = "reset"
    backend = TracedBackend(harness.create_backend(config), trace_path, time.monotonic() + WALL_CAP)
    runtime = _runtime_metadata(context)
    start = time.monotonic()
    termination = "normal"
    init = {"status": "success"}
    try:
        if runner == "bid":
            init = harness.init_project(_task_text(task), config, backend)
            if init.get("status") == "success":
                result = harness.run_project(config, backend)
            else:
                result = init
        else:
            init = _direct_init(task, config)
            result = harness.run_worker_session(1, config, backend=backend, task_base_state="s0")
            if result.get("status") == "submitted":
                result["status"] = "done"
        if backend.stop_reason:
            termination = backend.stop_reason
    except BudgetStop as exc:
        termination = str(exc)
        result = {"status": "budget_exhausted", "reason": termination}
    except Exception as exc:
        termination = type(exc).__name__
        result = {"status": "exception", "reason": str(exc)[:500]}
    wall = time.monotonic() - start
    if backend.stop_reason:
        termination = backend.stop_reason
    elif wall >= WALL_CAP:
        termination = "wall_cap"
    try:
        oracle = oracles.evaluate(task, workspace, PYTHON_HOME, REMOTE_ROOT / "oracle-tmp")
    except Exception as exc:
        oracle = {"task": task, "checks_passed": 0, "checks_total": 0, "binary_pass": False, "error": type(exc).__name__}
    events_path = workspace.parent / f"{workspace.name}-events" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
    lifecycle = {
        "worker_sessions": sum(e.get("event") == "worker_session" and e.get("phase") == "start" for e in events),
        "reviewer_verdicts": sum(e.get("event") == "reviewer_verdict" for e in events),
        "manager_decisions": sum(e.get("event") == "manager_decision" for e in events),
        "reworks": sum(e.get("event") in {"rework", "task_rework"} for e in events),
        "respawns": sum(e.get("event") == "respawn" for e in events),
        "run_commands_attempted": sum(e.get("event") == "command" and e.get("type") == "RUN" for e in events),
    }
    record = {
        "run_id": run_id, "task": task, "repeat": repeat, "condition": condition,
        "paired_reference": paired_with, "runner": runner, "context": context, "batch": batch,
        "status": result.get("status"), "termination": termination,
        "init_status": init.get("status"), "result": result,
        "wall_s": round(wall, 3), "request_count": backend.attempts,
        "successful_responses": backend.requests,
        "role_requests": backend.roles, "tokens": backend.tokens,
        "prompt_tokens": backend.prompt_tokens,
        "completion_tokens": backend.completion_tokens,
        "cached_prompt_tokens": backend.cached_prompt_tokens,
        "missing_usage_requests": backend.missing_usage,
        "token_cap": TOKEN_CAP, "token_cap_overshoot": max(0, backend.tokens - TOKEN_CAP),
        "wall_cap_s": WALL_CAP,
        "model_sha256": actual_model, "runtime": runtime,
        "protocol_treatment": "text-gbnf-lite" if CONSTRAINED else "unconstrained-text",
        "run_policy": "isolated-workspace" if SANDBOX_RUN else "denied",
        "model_path": MODEL_PATH, "model_alias": MODEL_ALIAS,
        "setup_s": float(os.environ.get("BID_CELL_SETUP_S", "0")),
        "decoding": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "repetition_penalty": 1.0, "thinking": True},
        "output_cap_per_request": OUTPUT_CAP,
        "oracle": oracle, "workspace_sha256": tree_hash(workspace),
        "trace_sha256": digest(trace_path) if trace_path.exists() else None,
        "source_epoch": SOURCE_EPOCH,
        "generation_seed": SEED + repeat,
        "source_manifest_sha256": digest(source_manifest_path()),
        "task_sha256": digest(TASK_ROOT / task / "task.md"),
        "oracle_sha256": digest(ROOT / "oracles.py"),
        "lifecycle": lifecycle,
        "events_sha256": digest(events_path) if events_path.exists() else None,
        "workspace_file_sha256": {
            path.relative_to(workspace).as_posix(): digest(path)
            for path, kind in workspace_entries(workspace) if kind == "file"
        },
        "workspace_symlinks": {
            path.relative_to(workspace).as_posix(): os.readlink(path)
            for path, kind in workspace_entries(workspace) if kind == "symlink"
        },
    }
    write_json(outcome_path, record)
    db.execute("UPDATE queue SET status=?,run_dir=?,result=? WHERE run_id=?", (termination, str(run_dir), str(outcome_path), run_id))
    db.commit()
    return record


def summarize():
    db = _connect()
    rows = db.execute("SELECT condition,task,status,result FROM queue ORDER BY condition,task,run_id").fetchall()
    cells = {}
    by_id = {}
    for condition, task, status, result_path in rows:
        cell = cells.setdefault((condition, task), {"queued": 0, "outcomes": 0, "binary_passes": 0, "partial_checks": 0, "tokens": [], "wall_s": [], "statuses": {}})
        cell["queued"] += 1
        cell["statuses"][status] = cell["statuses"].get(status, 0) + 1
        if not result_path or not pathlib.Path(result_path).exists():
            continue
        rec = json.loads(pathlib.Path(result_path).read_text(encoding="utf-8"))
        if "oracle" not in rec:
            continue
        cell["outcomes"] += 1
        cell["binary_passes"] += int(rec["oracle"].get("binary_pass", False))
        cell["partial_checks"] += rec["oracle"].get("checks_passed", 0)
        cell["tokens"].append(rec.get("tokens", 0))
        cell["wall_s"].append(rec.get("wall_s", 0))
        by_id[(condition, task, rec["repeat"])] = rec
    summary = {
        f"{condition}/{task}": {**value, "n": value["outcomes"]}
        for (condition, task), value in sorted(cells.items())
    }
    comparisons = {}
    for bid_condition, direct_condition in (("B128-B4", "D128"), ("B128-B1", "D128"), ("B64-B4", "D64")):
        for task in oracles.TASKS:
            pairs = [
                (by_id.get((bid_condition, task, repeat)), by_id.get((direct_condition, task, repeat)))
                for repeat in range(1, 4)
            ]
            pairs = [(bid, direct) for bid, direct in pairs if bid and direct]
            comparisons[f"{bid_condition}-vs-{direct_condition}/{task}"] = {
                "shared_direct_reference": True,
                "n_pairs": len(pairs),
                "bid_passes": sum(bool(bid["oracle"].get("binary_pass")) for bid, _ in pairs),
                "direct_passes": sum(bool(direct["oracle"].get("binary_pass")) for _, direct in pairs),
                "bid_only_wins": sum(bool(bid["oracle"].get("binary_pass")) and not direct["oracle"].get("binary_pass") for bid, direct in pairs),
                "direct_only_wins": sum(bool(direct["oracle"].get("binary_pass")) and not bid["oracle"].get("binary_pass") for bid, direct in pairs),
                "ties": sum(bool(bid["oracle"].get("binary_pass")) == bool(direct["oracle"].get("binary_pass")) for bid, direct in pairs),
                "bid_tokens": [bid.get("tokens", 0) for bid, _ in pairs],
                "direct_tokens": [direct.get("tokens", 0) for _, direct in pairs],
                "bid_wall_s": [bid.get("wall_s", 0) for bid, _ in pairs],
                "direct_wall_s": [direct.get("wall_s", 0) for _, direct in pairs],
            }
    summary = {"conditions": summary, "paired_comparisons": comparisons}
    target = REMOTE_ROOT / "summary.json"
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


def _signal_process_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return False
    return True


def _process_group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _supervise(command, timeout, output, cwd, env=None, terminate_grace=3):
    """Run one cell in an owned process group and kill leftovers on exit."""
    if os.name != "posix":
        raise OSError("study cell supervision requires POSIX process groups")
    with open(output, "ab", buffering=0) as log:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        timed_out = False
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_process_group(process.pid, signal.SIGTERM)
            try:
                returncode = process.wait(timeout=terminate_grace)
            except subprocess.TimeoutExpired:
                _signal_process_group(process.pid, signal.SIGKILL)
                returncode = process.wait()

        group_exists = _signal_process_group(process.pid, signal.SIGTERM)
        if group_exists:
            deadline = time.monotonic() + terminate_grace
            while time.monotonic() < deadline and _process_group_exists(process.pid):
                time.sleep(0.05)
            group_exists = _process_group_exists(process.pid)
            if group_exists:
                _signal_process_group(process.pid, signal.SIGKILL)
                deadline = time.monotonic() + terminate_grace
                while time.monotonic() < deadline and _process_group_exists(process.pid):
                    time.sleep(0.05)
        group_cleaned = not _process_group_exists(process.pid)
        if process.poll() is None:
            returncode = process.wait()
    return {"returncode": returncode, "timed_out": timed_out, "process_group_cleaned": group_cleaned}


def _run_cell_child(run_id):
    db = _connect()
    row = db.execute("SELECT * FROM queue WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise RuntimeError(f"unknown run id: {run_id}")
    _run_one(db, row)
    db.close()


def _run_supervised(db, row):
    run_id, task, repeat, condition = row[:4]
    _, context, _ = CONDITIONS[condition]
    setup_started = time.monotonic()
    _set_server_context(context)
    setup_s = time.monotonic() - setup_started
    run_dir = RUN_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "supervisor.log"
    env = os.environ | {"BID_CELL_SETUP_S": str(setup_s)}
    result = _supervise(
        [sys.executable, str(ROOT / "study.py"), "_run_cell", run_id],
        WALL_CAP + 30, log_path, ROOT, env=env,
    )
    outcome_path = run_dir / "outcome.json"
    if not outcome_path.exists():
        workspace = run_dir / "workspace"
        task_path = TASK_ROOT / task / "task.md"
        checkpoint = {
            "run_id": run_id, "task": task, "repeat": repeat,
            "condition": condition,
            "termination": "external_wall_cap" if result["timed_out"] else "child_exit_without_outcome",
            "supervisor": result, "setup_s": round(setup_s, 3),
            "trace_sha256": digest(run_dir / "trace.jsonl") if (run_dir / "trace.jsonl").exists() else None,
            "source_epoch": SOURCE_EPOCH,
            "source_manifest_sha256": digest(source_manifest_path()),
            "generation_seed": SEED + repeat,
            "model_sha256": digest(MODEL_PATH) if pathlib.Path(MODEL_PATH).is_file() else None,
            "task_sha256": digest(task_path),
            "oracle_sha256": digest(ROOT / "oracles.py"),
            "partial_workspace_sha256": tree_hash(workspace) if workspace.is_dir() else None,
        }
        write_json(outcome_path, checkpoint)
        db.execute("UPDATE queue SET status='incomplete',run_dir=?,result=? WHERE run_id=?", (str(run_dir), str(outcome_path), run_id))
        db.commit()
    else:
        record = json.loads(outcome_path.read_text(encoding="utf-8"))
        record["supervisor"] = result
        record["setup_s"] = round(setup_s, 3)
        write_json(outcome_path, record)
        db.execute("UPDATE queue SET status=?,run_dir=?,result=? WHERE run_id=?", (record.get("termination", "incomplete"), str(run_dir), str(outcome_path), run_id))
        db.commit()
    return json.loads(outcome_path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "summary", "_run_cell"))
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--limit", type=int, default=45, help="maximum queue entries to start")
    args = parser.parse_args()
    if args.command == "_run_cell":
        if not args.run_id:
            raise SystemExit("_run_cell requires a run id")
        _run_cell_child(args.run_id)
        return
    if args.command == "summary":
        summarize()
        return
    if time.time() >= CUTOFF_UTC - 600:
        raise SystemExit("study cutoff is within 10 minutes; no new runs")
    db = _connect()
    _schedule(db)
    _ensure_baselines()
    for run_id, task, repeat, condition, run_dir, result_path in db.execute(
        "SELECT run_id,task,repeat,condition,run_dir,result FROM queue WHERE status='running'"
    ).fetchall():
        directory = pathlib.Path(run_dir) if run_dir else RUN_ROOT / run_id
        directory.mkdir(parents=True, exist_ok=True)
        outcome = pathlib.Path(result_path) if result_path else directory / "outcome.json"
        if not outcome.exists():
            trace = directory / "trace.jsonl"
            checkpoint = {
                "run_id": run_id, "task": task, "repeat": repeat, "condition": condition,
                "termination": "interrupted", "checkpointed": True,
                "trace_sha256": digest(trace) if trace.exists() else None,
            }
            outcome.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
        db.execute("UPDATE queue SET status='incomplete',run_dir=?,result=? WHERE run_id=?", (str(directory), str(outcome), run_id))
        db.commit()
    rows = db.execute("SELECT * FROM queue WHERE status='pending' ORDER BY rowid LIMIT ?", (args.limit,)).fetchall()
    for row in rows:
        if time.time() >= CUTOFF_UTC - 600:
            print("cutoff guard reached; remaining queue preserved", flush=True)
            break
        db.execute("UPDATE queue SET status='running' WHERE run_id=?", (row[0],))
        db.commit()
        try:
            record = _run_supervised(db, row)
            print(json.dumps({"run_id": record["run_id"], "condition": record["condition"], "task": record["task"], "status": record.get("status", record.get("termination")), "pass": record.get("oracle", {}).get("binary_pass")}, sort_keys=True), flush=True)
        except Exception as exc:
            run_dir = RUN_ROOT / row[0]
            run_dir.mkdir(parents=True, exist_ok=True)
            checkpoint = {"run_id": row[0], "task": row[1], "repeat": row[2], "condition": row[3], "termination": type(exc).__name__, "reason": str(exc)[:500], "checkpointed": True}
            outcome_path = run_dir / "outcome.json"
            if not outcome_path.exists():
                checkpoint["source_manifest_sha256"] = digest(source_manifest_path())
                checkpoint["source_epoch"] = SOURCE_EPOCH
                write_json(outcome_path, checkpoint)
            db.execute("UPDATE queue SET status='incomplete',run_dir=?,result=? WHERE run_id=?", (str(run_dir), str(run_dir / "outcome.json"), row[0]))
            db.commit()
            print(json.dumps(checkpoint, sort_keys=True), flush=True)
            break


if __name__ == "__main__":
    main()
