import re
import os
import shutil

from . import adapter as adapter_mod
from . import model as model_mod
from . import todo as todo_mod
from . import vc as vc_mod


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def get_config():
    return {
        "endpoint": os.environ.get("BID_MODEL_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions"),
        "model_name": os.environ.get("BID_MODEL_NAME", "smollm3-3b"),
        "max_tokens": int(os.environ.get("BID_MAX_TOKENS", "8192")),
        "workspace": os.environ.get("BID_WORKSPACE", os.path.join(os.getcwd(), "workspace")),
        "request_timeout": int(os.environ.get("BID_REQUEST_TIMEOUT", "300")),
        "inactivity_timeout": int(os.environ.get("BID_INACTIVITY_TIMEOUT", "600")),
        "worker_timeout": int(os.environ.get("BID_WORKER_TIMEOUT", "3600")),
        "repeat_action_limit": int(os.environ.get("BID_REPEAT_ACTION_LIMIT", "5")),
        "max_searches_per_worker": int(os.environ.get("BID_MAX_SEARCHES", "10")),
        "search_endpoint": os.environ.get("BID_SEARCH_ENDPOINT", ""),
    }


def create_backend(config):
    if os.environ.get("BID_BACKEND") == "mock":
        return model_mod.MockBackend()
    return model_mod.LlamaCppBackend(
        endpoint=config["endpoint"],
        model=config["model_name"],
        timeout=config["request_timeout"],
        text_tools=os.environ.get("BID_TEXT_TOOLS", "1") == "1",
        max_tokens=config["max_tokens"],
    )


def load_prompt(name):
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def read_file_content(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def write_file_content(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


def _append_missing_tasks(todo_text, missing):
    seen = {task["description"].strip().lower() for task in todo_mod.parse_todo(todo_text)}
    next_number = max((task["number"] for task in todo_mod.parse_todo(todo_text)), default=0)
    for desc in missing:
        item = desc.strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        next_number += 1
        todo_text += f"\n- [ ] T{next_number} — {item}\n"
    return todo_text


def _task_base_state(vc_system):
    current = vc_system.get_current()
    if not current:
        return None
    log = vc_system.get_log()
    marker = f"### {current}\n"
    start = log.rfind(marker)
    if start < 0:
        return current
    section = log[start + len(marker):]
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## ") or stripped.startswith("### "):
            break
        match = re.search(r"\bbase=(s\d+)\b", stripped)
        if match:
            return match.group(1)
    return current


def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    write_file_content(os.path.join(docs, "manager.md"), load_prompt("manager"))
    write_file_content(os.path.join(docs, "worker.md"), load_prompt("worker"))


# ── Worker session ───────────────────────────────────────────────────

def run_worker_session(number, config, backend=None, feedback=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    base_state = vc_system.get_current()

    try:
        worker_adapter = adapter_mod.WorkerAdapter(config, number, feedback=feedback)
        b = backend or create_backend(config)
        result = worker_adapter.run(b)
    except Exception as exc:
        result = {"status": "error", "reason": str(exc)}

    if result.get("status") == "done":
        try:
            state = vc_system.save_state(
                f"Worker {number}",
                f"T{number} candidate base={base_state}",
            )
        except Exception:
            if base_state:
                vc_system.restore(base_state, preserve_todo=True)
            return {"status": "error", "reason": "vc save failed after worker"}
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": "normal",
            "state": state,
            "base_state": base_state,
        }

    if base_state:
        try:
            vc_system.restore(base_state, preserve_todo=True)
        except Exception as exc:
            return {"status": "error", "reason": f"rollback failed: {exc}"}
    return {
        "status": "error",
        "reason": result.get("reason", f"T{number} was not submitted"),
        "termination": result.get("status", "error"),
    }


# ── Init ─────────────────────────────────────────────────────────────

def init_project(user_task, config, backend=None):
    workspace = config["workspace"]
    parent = os.path.dirname(workspace)
    backup_dir = os.path.join(parent, ".bid_backup") if parent else "/tmp/.bid_backup"

    # Transactional: rename existing workspace to backup, restore on failure
    ws_exists = os.path.exists(workspace)
    if ws_exists:
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        os.rename(workspace, backup_dir)

    try:
        os.makedirs(workspace, exist_ok=True)
        ensure_workspace(workspace)
        write_file_content(os.path.join(workspace, "docs/task.md"), f"# Task\n\n{user_task}\n")
        write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
        write_file_content(os.path.join(workspace, "docs/decisions.md"), "# Decisions\n\n")

        vc_system = vc_mod.VersionControl(workspace)
        vc_system.init()

        adp = adapter_mod.ManagerInitAdapter(config)
        b = backend or create_backend(config)
        result = adp.run(b)
    except Exception as exc:
        # Restore backup
        if ws_exists:
            shutil.rmtree(workspace, ignore_errors=True)
            os.rename(backup_dir, workspace)
        return {"status": "error", "reason": str(exc)}

    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    if result["status"] == "success" and tasks:
        try:
            state = vc_system.save_state("Manager (init)", "Project initialized")
        except Exception as exc:
            if ws_exists:
                shutil.rmtree(workspace, ignore_errors=True)
                os.rename(backup_dir, workspace)
            return {"status": "error", "reason": str(exc)}
        # Success - delete backup
        if ws_exists and os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
        return {"status": "success", "state": state}

    # Init failed but didn't raise
    if ws_exists:
        shutil.rmtree(workspace, ignore_errors=True)
        os.rename(backup_dir, workspace)
    return {"status": "error", "reason": result.get("reason", "Manager did not create a valid TODO")}


# ── Project runner ───────────────────────────────────────────────────

def run_project(config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    backend = backend or create_backend(config)
    reviewer_feedback = {}
    current_task_number = None
    current_task_base_state = None

    while True:
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)

        if not tasks:
            return {"status": "error", "reason": "no TODO tasks"}

        if todo_mod.all_checked(tasks):
            completion = adapter_mod.CompletionReviewAdapter(config).run(backend)
            if completion.get("verdict") == "COMPLETE":
                return {"status": "done"}
            if completion.get("verdict") == "MISSING":
                todo_text = _append_missing_tasks(todo_text, completion.get("missing", []))
                write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
                current_task_number = None
                current_task_base_state = None
                continue
            return {"status": "error", "reason": completion.get("reason", "completion review error"), "detail": completion}

        unchecked = todo_mod.first_unchecked(tasks)

        if unchecked is None:
            return {"status": "error", "reason": "no unchecked task found"}

        number = unchecked["number"]
        if current_task_number != number or current_task_base_state is None:
            current_task_number = number
            current_task_base_state = _task_base_state(vc_system)
        print(f"Worker {number}...")
        try:
            result = run_worker_session(number, config, backend=backend, feedback=reviewer_feedback.get(number))
        except Exception as exc:
            return {"status": "error", "reason": f"Worker {number} exception: {exc}"}
        if result["status"] != "submitted":
            print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
            return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
        print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")

        review = None
        try:
            review = adapter_mod.TaskReviewAdapter(config, number, base_state=current_task_base_state).run(backend)
        except Exception as exc:
            return {"status": "error", "reason": f"review exception: {exc}"}

        if review.get("verdict") == "ERROR":
            return {"status": "error", "reason": review.get("reason", "review error"), "detail": review}

        if review.get("verdict") == "REWORK":
            reviewer_feedback[number] = review.get("reason", "")
            print(f"Worker {number} rework: {reviewer_feedback[number]}")
            continue

        if review.get("verdict") == "ACCEPT":
            reviewer_feedback.pop(number, None)
            todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
            todo_text = todo_mod.set_task_checked(todo_text, number, True)
            write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
            current_task_number = None
            current_task_base_state = None

            if todo_mod.all_checked(todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))):
                completion = adapter_mod.CompletionReviewAdapter(config).run(backend)
                if completion.get("verdict") == "COMPLETE":
                    return {"status": "done"}
                if completion.get("verdict") == "MISSING":
                    todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
                    todo_text = _append_missing_tasks(todo_text, completion.get("missing", []))
                    write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
                    continue
                return {"status": "error", "reason": completion.get("reason", "completion review error"), "detail": completion}

        return {"status": "error", "reason": f"unexpected review verdict: {review.get('verdict', '?')}"}


def show_status(config):
    workspace = config["workspace"]
    if not os.path.exists(os.path.join(workspace, ".bid")):
        print("No BID project in workspace.")
        return
    current = vc_mod.VersionControl(workspace).get_current() or "?"
    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    checked = sum(1 for task in tasks if task["checked"])
    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{len(tasks)} checked")
    print(f"Done:     {'yes' if tasks and todo_mod.all_checked(tasks) else 'no'}")
    for task in tasks:
        mark = "x" if task["checked"] else " "
        print(f"  [{mark}] {task['id']} — {task['description']}")
