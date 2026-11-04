"""One bounded real-model development smoke; not a benchmark cell."""

import hashlib
import json
import pathlib
import sys
import time

import study
from bid import harness, todo, vc


def main():
    root = pathlib.Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    config = harness.get_config()
    config.update(
        workspace=str(workspace),
        endpoint="http://127.0.0.1:8081/v1/chat/completions",
        model_name=study.MODEL_ALIAS,
        max_tokens=4096,
        request_timeout=45,
        worker_timeout=90,
        inactivity_timeout=60,
        provisional_batch=1,
        max_task_reworks=1,
        max_manager_reworks=1,
        allow_host_run=False,
        max_searches_per_worker=0,
        seed=20261124,
    )
    backend = study.TracedBackend(
        harness.create_backend(config), root / "trace.jsonl",
        time.monotonic() + 120, token_cap=30_000,
    )
    task = (
        "Create hello.txt containing exactly the single line `hello from BID` "
        "with one final newline. Create no other project files. One checklist "
        "item is sufficient."
    )
    (root / "task.txt").write_text(task + "\n", encoding="utf-8")
    started = time.monotonic()
    initial = harness.init_project(task, config, backend)
    result = harness.run_project(config, backend) if initial["status"] == "success" else initial
    artifact = workspace / "hello.txt"
    artifact_bytes = artifact.read_bytes() if artifact.is_file() else None
    todo_text = (workspace / "docs/todo.md").read_text(encoding="utf-8") if (workspace / "docs/todo.md").exists() else ""
    log_text = vc.VersionControl(str(workspace)).get_log() if (workspace / ".bid").exists() else ""
    tasks = todo.parse_todo(todo_text)
    record = {
        "identity": "development-smoke-2026-11-02-epoch20-source-normal-text",
        "source_epoch": study.SOURCE_EPOCH,
        "source_manifest_sha256": study.digest(study.source_manifest_path()),
        "model_alias": study.MODEL_ALIAS,
        "model_sha256": "b5607b5090a8280063fff2d706bb3408ca6542341b06aab39c3eca0a28575921",
        "seed": config["seed"],
        "context": study._server_context(),
        "protocol": "product prompts and text command parser; no grammar override",
        "run_policy": "RUN disabled",
        "task": task,
        "init": initial,
        "result": result,
        "wall_s": round(time.monotonic() - started, 3),
        "roles": backend.roles,
        "tokens": backend.tokens,
        "requests": backend.requests,
        "artifact_hex": artifact_bytes.hex() if artifact_bytes is not None else None,
        "artifact_pass": artifact_bytes == b"hello from BID\n",
        "todo": todo_text,
        "vc_log": log_text,
        "full_loop": (
            result.get("status") == "done"
            and bool(tasks)
            and all(task["state"] == "done" for task in tasks)
            and "provisional: task=T1" in log_text
            and "ratified: task=T1" in log_text
            and backend.roles.get("reviewer", 0) > 0
            and backend.roles.get("manager_reconcile", 0) > 0
        ),
    }
    trace = root / "trace.jsonl"
    record["trace_sha256"] = hashlib.sha256(trace.read_bytes()).hexdigest()
    (root / "outcome.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    main()
