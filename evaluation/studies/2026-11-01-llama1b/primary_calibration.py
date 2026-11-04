"""One independently scored full-role calibration, run under the study supervisor."""

import hashlib
import json
import pathlib
import sys
import time

import study
from bid import harness


def main():
    root = pathlib.Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    config = harness.get_config()
    config.update(
        workspace=str(workspace), endpoint="http://127.0.0.1:8081/v1/chat/completions",
        model_name=study.MODEL_ALIAS, max_tokens=8192, request_timeout=60,
        worker_timeout=180, inactivity_timeout=120, provisional_batch=1,
        allow_host_run=False, max_searches_per_worker=0, max_task_reworks=2,
        max_manager_reworks=2, seed=int(sys.argv[2]),
    )
    study.configure_execution(config)
    backend = study.TracedBackend(
        harness.create_backend(config), root / "trace.jsonl", time.monotonic() + 180,
        token_cap=50_000,
    )
    task = (
        "Write the uppercase word COVE to marker.txt. Use the four letters themselves, not an escaped representation. Do not add a newline. "
        "No other project artifacts are needed. One file-producing checklist item is sufficient."
    )
    (root / "task.txt").write_text(task, encoding="utf-8")
    started = time.monotonic()
    initial = harness.init_project(task, config, backend)
    result = harness.run_project(config, backend) if initial["status"] == "success" else initial
    marker = workspace / "marker.txt"
    content = marker.read_bytes() if marker.is_file() else None
    record = {
        "source_epoch": study.SOURCE_EPOCH,
        "source_manifest_sha256": study.digest(study.source_manifest_path()),
        "model_alias": study.MODEL_ALIAS, "seed": config["seed"],
        "context": study._server_context(), "protocol": "text-gbnf-lite",
        "run_policy": "isolated-workspace", "init": initial, "result": result,
        "wall_s": time.monotonic() - started, "roles": backend.roles,
        "tokens": backend.tokens, "requests": backend.requests,
        "marker_bytes": content.decode("utf-8", "replace") if content is not None else None,
        "oracle_pass": content == b"COVE", "full_loop": (
            content == b"COVE" and result["status"] == "done"
            and backend.roles.get("reviewer", 0) > 0
            and backend.roles.get("manager_reconcile", 0) > 0
        ),
    }
    record["trace_sha256"] = hashlib.sha256((root / "trace.jsonl").read_bytes()).hexdigest()
    (root / "outcome.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    main()
