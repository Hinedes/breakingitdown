"""Bounded CLI-to-llama.cpp development smoke with raw HTTP tracing."""

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bid import todo, vc


TASK = (
    "Create hello.txt containing exactly the single line `hello from BID`, "
    "with one final newline. Do not create any other files."
)
UPSTREAM = "http://127.0.0.1:8081"


def main():
    root = pathlib.Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    trace_path = root / "trace.jsonl"
    records = []
    lock = threading.Lock()

    class ProxyHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request_data = json.loads(payload)
            systems = "\n".join(
                message.get("content", "")
                for message in request_data.get("messages", [])
                if message.get("role") == "system"
            ).lower()
            role = (
                "reviewer" if "task reviewer" in systems else
                "manager_reconcile" if "project reconciliation" in systems else
                "manager" if "manager" in systems else "worker"
            )
            record = {"request": request_data, "role": role}
            try:
                upstream = Request(
                    UPSTREAM + self.path, payload,
                    {"Content-Type": "application/json"},
                )
                with urlopen(upstream, timeout=120) as response:
                    status, body = response.status, response.read()
            except HTTPError as exc:
                status, body = exc.code, exc.read()
            except (OSError, URLError) as exc:
                status, body = 502, json.dumps({"proxy_error": str(exc)}).encode()
            record.update(status=status, response=body.decode("utf-8", "replace"))
            with lock:
                with trace_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                records.append(record)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 8082), ProxyHandler)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    env.update({
        "BID_MODEL_ENDPOINT": "http://127.0.0.1:8082/v1/chat/completions",
        "BID_MODEL_NAME": "Llama-3.2-3B-Instruct-Q8_0",
        "BID_WORKSPACE": str(workspace),
        "BID_MAX_TOKENS": "2048",
        "BID_REQUEST_TIMEOUT": "20",
        "BID_WORKER_TIMEOUT": "45",
        "BID_INACTIVITY_TIMEOUT": "30",
        "BID_REPEAT_ACTION_LIMIT": "3",
        "BID_MAX_TASK_REWORKS": "1",
        "BID_MAX_MANAGER_REWORKS": "1",
        "BID_PROVISIONAL_BATCH": "1",
        "BID_MAX_SEARCHES": "0",
        "BID_PROTOCOL_GRAMMAR": "1",
    })
    env.pop("BID_ALLOW_HOST_RUN", None)
    cli = pathlib.Path(__file__).resolve().parents[2] / "bid.py"
    commands = []
    started = time.monotonic()
    try:
        init = subprocess.run(
            [sys.executable, str(cli), "init", TASK], cwd=cli.parent,
            env=env, capture_output=True, text=True, timeout=180,
        )
        commands.append({"command": "bid init <task>", "returncode": init.returncode,
                         "stdout": init.stdout, "stderr": init.stderr})
        run = None
        if init.returncode == 0:
            run = subprocess.run(
                [sys.executable, str(cli), "run"], cwd=cli.parent,
                env=env, capture_output=True, text=True, timeout=180,
            )
            commands.append({"command": "bid run", "returncode": run.returncode,
                             "stdout": run.stdout, "stderr": run.stderr})
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)

    artifact = workspace / "hello.txt"
    artifact_bytes = artifact.read_bytes() if artifact.is_file() else None
    todo_path = workspace / "docs/todo.md"
    todo_text = todo_path.read_text(encoding="utf-8") if todo_path.exists() else ""
    log_text = vc.VersionControl(str(workspace)).get_log() if (workspace / ".bid").exists() else ""
    tasks = todo.parse_todo(todo_text)
    roles = {}
    for record in records:
        roles[record["role"]] = roles.get(record["role"], 0) + 1
    run_ok = bool(commands and commands[-1]["command"] == "bid run" and commands[-1]["returncode"] == 0)
    outcome = {
        "identity": "development-smoke-2026-11-02-cli-grammar-product-config",
        "source_identity": os.environ.get("BID_DEV_SOURCE_ID", "unrecorded"),
        "source_manifest_sha256": os.environ.get("BID_DEV_SOURCE_MANIFEST_SHA256"),
        "task": TASK,
        "config": {key: env[key] for key in (
            "BID_MODEL_ENDPOINT", "BID_MODEL_NAME", "BID_MAX_TOKENS",
            "BID_REQUEST_TIMEOUT", "BID_WORKER_TIMEOUT", "BID_INACTIVITY_TIMEOUT",
            "BID_REPEAT_ACTION_LIMIT", "BID_MAX_TASK_REWORKS",
            "BID_MAX_MANAGER_REWORKS", "BID_PROVISIONAL_BATCH",
            "BID_MAX_SEARCHES", "BID_PROTOCOL_GRAMMAR",
        )},
        "commands": commands,
        "roles": roles,
        "requests": len(records),
        "wall_s": round(time.monotonic() - started, 3),
        "artifact_hex": artifact_bytes.hex() if artifact_bytes is not None else None,
        "artifact_pass": artifact_bytes == b"hello from BID\n",
        "todo": todo_text,
        "vc_log": log_text,
        "full_loop": (
            run_ok and bool(tasks) and all(task["state"] == "done" for task in tasks)
            and "provisional: task=T1" in log_text
            and "ratified: task=T1" in log_text
            and roles.get("reviewer", 0) > 0
            and roles.get("manager_reconcile", 0) > 0
        ),
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    }
    (root / "outcome.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, indent=2), flush=True)


if __name__ == "__main__":
    main()
