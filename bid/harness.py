import os
import json

from . import model as model_mod
from . import todo as todo_mod
from . import vc as vc_mod
from . import permissions
from . import tools as tools_mod
from . import session
from .observer import Observer

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def get_config():
    return {
        "endpoint": os.environ.get("BID_MODEL_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions"),
        "model_name": os.environ.get("BID_MODEL_NAME", "smollm3-3b"),
        "max_tokens": int(os.environ.get("BID_MAX_TOKENS", "8192")),
        "workspace": os.environ.get("BID_WORKSPACE", os.path.join(os.getcwd(), "workspace")),
        "timeout": int(os.environ.get("BID_REQUEST_TIMEOUT", "120")),
        "request_timeout": int(os.environ.get("BID_REQUEST_TIMEOUT", "300")),
        "inactivity_timeout": int(os.environ.get("BID_INACTIVITY_TIMEOUT", "600")),
        "worker_timeout": int(os.environ.get("BID_WORKER_TIMEOUT", "3600")),
    }


def create_backend(config):
    if os.environ.get("BID_BACKEND") == "mock":
        return model_mod.MockBackend()
    text_tools = os.environ.get("BID_TEXT_TOOLS", "1") == "1"
    max_tokens = int(os.environ.get("BID_MAX_TOKENS", "8192"))
    return model_mod.LlamaCppBackend(
        endpoint=config["endpoint"],
        model=config["model_name"],
        timeout=config["timeout"],
        text_tools=text_tools,
        max_tokens=max_tokens,
    )


def load_prompt(name):
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def read_file_content(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_file_content(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


MANAGER_INSTRUCTIONS = """You are the BID Manager.

Break the user's task into numbered items and write them ALL to docs/todo.md in a single write_file call.

Format (write all tasks at once):
- [ ] T1 — description
- [ ] T2 — description
- [ ] T3 — description

Each task must be a single focused unit. Keep descriptions concise.

When reviewing completed work: read the artifacts, compare against requirements, accept (write DONE to docs/project-status.md) or uncheck inadequate tasks with repair notes.

You may read all files. Write only: docs/task.md, docs/todo.md, docs/project-status.md, docs/decisions.md.

Never perform Worker work. When done, call finish.
"""

WORKER_INSTRUCTIONS = """# Worker

You have one task.

Read only what is needed.
Create or repair the required artifact.
Do not perform other tasks.
You may revise your work after checking your task.
When no further correction is needed, output `Done`.

You check your task box by editing docs/todo.md with write_file.
Only change your own task checkbox.
"""


def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    mgr_path = os.path.join(docs, "manager.md")
    if not os.path.exists(mgr_path):
        write_file_content(mgr_path, MANAGER_INSTRUCTIONS)
    wrk_path = os.path.join(docs, "worker.md")
    if not os.path.exists(wrk_path):
        write_file_content(wrk_path, WORKER_INSTRUCTIONS)


def run_agent(messages, tools, backend, config, workspace, role, worker_number=None):
    reset = getattr(tools_mod, "reset_write_tracking", lambda w: None)
    reset(workspace)
    hard_ceiling = config.get("worker_timeout", 3600)
    inactivity_sec = config.get("inactivity_timeout", 600)
    obs = Observer(workspace, worker_number or 0)
    obs.snapshot_tree()
    obs.mark_activity()

    while obs.elapsed() < hard_ceiling:
        result = session.run_turn(messages, tools, backend, config, workspace, role, worker_number)
        content = result["content"]

        if content:
            obs.mark_activity()
        if result["tool_calls"]:
            obs.mark_activity()

        if obs.seen_done(content):
            return {"status": "done", "messages": messages}

        if obs.inactive_for() > inactivity_sec:
            return {"status": "timeout", "reason": f"inactive {obs.inactive_for():.0f}s", "messages": messages}

    return {"status": "timeout", "reason": f"hard ceiling {hard_ceiling}s", "messages": messages}


def run_manager_init(config, backend=None):
    ws = config["workspace"]
    prompt = load_prompt("manager")
    task_text = read_file_content(os.path.join(ws, "docs/task.md"))
    assignment = (
        "Read docs/manager.md and docs/task.md. "
        "Then write docs/todo.md with numbered tasks T1, T2, T3,... "
        "Each line: - [ ] T1 — description. Then output: Done"
    )
    tools = tools_mod.get_tools_for_role("manager")
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": assignment}]
    if backend is None:
        backend = create_backend(config)
    return run_agent(messages, tools, backend, config, ws, "manager")


def run_manager_review(config, backend=None):
    ws = config["workspace"]
    prompt = load_prompt("manager")
    assignment = (
        "Read docs/manager.md, docs/task.md, docs/todo.md and "
        "docs/project-status.md. Inspect the relevant Worker artifacts. "
        "Accept, reopen, repair, or extend the work, then output: Done"
    )
    tools = tools_mod.get_tools_for_role("manager")
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": assignment}]
    if backend is None:
        backend = create_backend(config)
    return run_agent(messages, tools, backend, config, ws, "manager")


def run_worker_session(number, config, backend=None):
    ws = config["workspace"]
    prompt = f"/no_think\nBID Worker {number}."
    assignment = (
        f"Read docs/worker.md. Perform Task T{number} from docs/todo.md. "
        f"When ready, ensure T{number} is checked in docs/todo.md and output: Done"
    )
    tools = tools_mod.get_tools_for_role("worker", worker_number=number)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": assignment}]
    if backend is None:
        backend = create_backend(config)
    result = run_agent(messages, tools, backend, config, ws, "worker", number)

    if result["status"] != "done":
        return result

    obs = Observer(ws, number)
    if obs.task_is_checked():
        vc_sys = vc_mod.VersionControl(ws)
        vc_sys.save_state(f"Worker {number}", f"T{number} submitted")
        return {"status": "submitted", "summary": f"T{number} submitted"}

    return {"status": "error", "reason": f"T{number} not checked"}


def init_project(user_task, config, backend=None):
    ws = config["workspace"]
    ensure_workspace(ws)

    write_file_content(os.path.join(ws, "docs/task.md"), f"# Task\n\n{user_task}\n")
    write_file_content(os.path.join(ws, "docs/todo.md"), "# TODO\n\n")
    write_file_content(os.path.join(ws, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
    write_file_content(os.path.join(ws, "docs/decisions.md"), "# Decisions\n\n")

    vc_sys = vc_mod.VersionControl(ws)
    vc_sys.init()

    result = run_manager_init(config, backend=backend)
    if result["status"] == "done":
        vc_sys.save_state("Manager (init)", "Project initialized")
        return {"status": "success"}
    return {"status": "error", "reason": "Manager init failed"}


def run_project(config, backend=None):
    ws = config["workspace"]
    vc_sys = vc_mod.VersionControl(ws)

    if backend is None:
        backend = create_backend(config)

    while True:
        todo_text = read_file_content(os.path.join(ws, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        status_text = read_file_content(os.path.join(ws, "docs/project-status.md"))

        unchecked = todo_mod.first_unchecked(tasks)
        if unchecked is not None:
            number = unchecked["number"]
            print(f"Worker {number}...")
            result = run_worker_session(number, config, backend=backend)
            if result["status"] == "submitted":
                print(f"Worker {number} submitted T{number}.")
            else:
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                current = vc_sys.get_current()
                if current:
                    vc_sys.restore(current)
                return {"status": "error", "reason": f"Worker {number} failed"}
            continue

        if "DONE" in status_text:
            return {"status": "done"}

        print("All tasks checked. Running Manager review...")
        result = run_manager_review(config, backend=backend)
        if result["status"] != "done":
            vc_sys.save_state("Manager (review)", "Review completed")
            continue

        print("Manager produced no further action. Pausing.")
        return {"status": "paused"}


def show_status(config):
    ws = config["workspace"]
    if not os.path.exists(os.path.join(ws, ".bid")):
        print("No BID project in workspace.")
        return
    vc_sys = vc_mod.VersionControl(ws)
    current = vc_sys.get_current() or "?"
    todo_text = read_file_content(os.path.join(ws, "docs/todo.md"))
    tasks = todo_mod.parse_todo(todo_text) if todo_text else []
    checked = sum(1 for t in tasks if t["checked"])
    total = len(tasks)
    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{total} checked")
    for t in tasks:
        mark = "x" if t["checked"] else " "
        print(f"  [{mark}] {t['id']} — {t['description']}")
