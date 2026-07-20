import os

from . import model as model_mod
from . import todo as todo_mod
from . import vc as vc_mod
from . import permissions
from . import tools as tools_mod
from . import session


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def get_config():
    return {
        "endpoint": os.environ.get("BID_MODEL_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions"),
        "model_name": os.environ.get("BID_MODEL_NAME", "smollm3-3b"),
        "max_turns": int(os.environ.get("BID_MAX_AGENT_TURNS", "50")),
        "max_tokens": int(os.environ.get("BID_MAX_TOKENS", "8192")),
        "workspace": os.environ.get("BID_WORKSPACE", os.path.join(os.getcwd(), "workspace")),
        "timeout": int(os.environ.get("BID_REQUEST_TIMEOUT", "120")),
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

Call write_file ONCE with the full content of docs/todo.md containing all tasks:

- [ ] T1 — First task
- [ ] T2 — Second task
- [ ] T3 — Third task

ONE write_file call with all tasks in the content. Do not call write_file multiple times.

When reviewing: read artifacts, check requirements, accept (DONE) or uncheck/repair.

Write only: docs/task.md, docs/todo.md, docs/project-status.md, docs/decisions.md.

Never do Worker work. When done, finish.
"""

WORKER_INSTRUCTIONS = """You are a BID Worker.

Read docs/todo.md and locate your assigned task.
Read only the project material needed for it.
Perform only that task.
Write a complete artifact.
When the work is ready for Manager review, call submit_task once.

If you cannot complete it, do not submit it.
Record the blocker in a Worker-owned artifact and allow the session to fail.
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


def run_manager_session(task_text, todo_text, status_text, backend, config, workspace, mode="init"):
    prompt = load_prompt("manager")
    if mode == "init":
        assignment = (
            "Read docs/manager.md and docs/task.md. "
            "Then write docs/todo.md with numbered tasks T1, T2, T3,... "
            "Each line: - [ ] T1 — description. Then finish."
        )
    else:
        assignment = (
            "Read docs/manager.md, docs/task.md, docs/todo.md and "
            "docs/project-status.md. Inspect the relevant Worker artifacts. "
            "Accept, reopen, repair, or extend the work, then finish."
        )
    tools = tools_mod.get_tools_for_role(permissions.ROLE_MANAGER)
    return session.run_session(prompt, assignment, tools, backend, config, workspace, permissions.ROLE_MANAGER)


def run_worker_session(number, backend, config, workspace):
    prompt = "/no_think\nBID Worker. JSON tool calls only."
    assignment = f"You are Worker {number}. Read docs/worker.md, then perform Task T{number}. Call submit_task when done."
    tools = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=number)
    return session.run_session(prompt, assignment, tools, backend, config, workspace, permissions.ROLE_WORKER, worker_number=number)


def init_project(user_task, config, backend=None):
    ws = config["workspace"]
    ensure_workspace(ws)

    write_file_content(os.path.join(ws, "docs/task.md"), f"# Task\n\n{user_task}\n")
    write_file_content(os.path.join(ws, "docs/todo.md"), "# TODO\n\n")
    write_file_content(os.path.join(ws, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
    write_file_content(os.path.join(ws, "docs/decisions.md"), "# Decisions\n\n")

    vc_sys = vc_mod.VersionControl(ws)
    vc_sys.init()

    if backend is None:
        backend = create_backend(config)

    result = run_manager_session(user_task, "", "", backend, config, ws, mode="init")
    if result["status"] == "success":
        vc_sys.save_state("Manager (init)", result.get("summary", ""))

    return result


def run_project(config, backend=None):
    ws = config["workspace"]
    vc_sys = vc_mod.VersionControl(ws)

    if backend is None:
        backend = create_backend(config)

    while True:
        task_text = read_file_content(os.path.join(ws, "docs/task.md"))
        todo_text = read_file_content(os.path.join(ws, "docs/todo.md"))
        status_text = read_file_content(os.path.join(ws, "docs/project-status.md"))
        tasks = todo_mod.parse_todo(todo_text)

        unchecked = todo_mod.first_unchecked(tasks)
        if unchecked is not None:
            current = vc_sys.get_current()
            number = unchecked["number"]
            print(f"Running Worker {number}...")
            result = run_worker_session(number, backend, config, ws)
            if result["status"] == "success":
                post_todo = read_file_content(os.path.join(ws, "docs/todo.md"))
                post_tasks = todo_mod.parse_todo(post_todo)
                post_task = todo_mod.get_task(post_tasks, number)
                if post_task and post_task["checked"]:
                    vc_sys.save_state(f"Worker {number}", result.get("summary", ""))
                    print(f"Worker {number} finished.")
                else:
                    if current:
                        vc_sys.restore(current)
                        print(f"Worker {number} did not check T{number}. Restored {current}.")
                    return {"status": "error", "reason": f"Worker {number} did not check T{number}"}
            else:
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                if current:
                    vc_sys.restore(current)
                    print(f"Restored state {current}.")
                return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
            continue

        print("All tasks checked. Running Manager review...")
        result = run_manager_session(task_text, todo_text, status_text, backend, config, ws, mode="review")
        if result["status"] != "success":
            print(f"Manager review failed: {result.get('reason', 'unknown')}")
            return {"status": "error", "reason": "Manager review failed", "detail": result}

        vc_sys.save_state("Manager (review)", result.get("summary", ""))

        status_text = read_file_content(os.path.join(ws, "docs/project-status.md"))
        if "DONE" in status_text:
            return {"status": "done", "summary": result.get("summary", "")}

        todo_text = read_file_content(os.path.join(ws, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        if todo_mod.first_unchecked(tasks) is not None:
            print("Manager created new or reopened tasks. Continuing...")
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
    status_text = read_file_content(os.path.join(ws, "docs/project-status.md"))
    task_text = read_file_content(os.path.join(ws, "docs/task.md"))

    tasks = todo_mod.parse_todo(todo_text) if todo_text else []
    checked = sum(1 for t in tasks if t["checked"])
    total = len(tasks)

    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{total} checked")
    for t in tasks:
        mark = "x" if t["checked"] else " "
        print(f"  [{mark}] {t['id']} — {t['description']}")
    print()
    lines = status_text.strip().split("\n") if status_text.strip() else ["(empty)"]
    print("Project status:")
    for l in lines[:5]:
        print(f"  {l}")
