import hashlib
import os
import shutil
import time
import tempfile

from . import adapter as adapter_mod
from . import model as model_mod
from . import permissions
from . import todo as todo_mod
from . import vc as vc_mod
from .observer import Observer


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")

MANAGER_INSTRUCTIONS = """# Manager

You manage the project but do not perform Worker tasks.

Initialization:
- Read docs/task.md.
- Break the request into sequential numbered tasks.
- Write the complete checklist to docs/todo.md.

Review:
- Read the original task, checklist, and relevant Worker artifacts.
- Write DONE to docs/project-status.md only when the whole request is satisfied.
- Otherwise uncheck inadequate tasks or add missing tasks in docs/todo.md.

When the current Manager job is complete, output exactly `Done`.
"""

WORKER_INSTRUCTIONS = """# Worker

You have one task.

- Read docs/todo.md and the minimum other material needed.
- Perform only your assigned task.
- Create or repair its artifact.
- To submit, rewrite docs/todo.md changing only your own checkbox from `[ ]` to `[x]`.
- You may keep working after checking it, revise files, or uncheck it again while backtracking.
- When the final submitted state is ready, keep your checkbox checked and output exactly `Done`.
"""


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


def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    write_file_content(os.path.join(docs, "manager.md"), MANAGER_INSTRUCTIONS)
    write_file_content(os.path.join(docs, "worker.md"), WORKER_INSTRUCTIONS)


def _todo_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


_COMPLETED_HASH_FILE = "docs/.completed_hash"


def _compute_plan_hash(workspace, tasks):
    """Return SHA-256 of (TODO + sorted artifact paths + each artifact content)."""
    todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
    h = _todo_hash(todo_text)
    for t in tasks:
        out, _ = todo_mod.get_task_metadata(tasks, t["number"])
        p = os.path.join(workspace, out)
        if os.path.exists(p):
            h += _todo_hash(read_file_content(p))
    return _todo_hash(h)


def _is_completed(workspace, tasks):
    """DONE is valid only when persisted hash matches current state."""
    status_raw = read_file_content(os.path.join(workspace, "docs/project-status.md")).strip()
    if status_raw != "# Project Status\n\nDONE":
        return False
    hash_path = os.path.join(workspace, _COMPLETED_HASH_FILE)
    if not os.path.exists(hash_path):
        return False
    stored = read_file_content(hash_path).strip()
    current = _compute_plan_hash(workspace, tasks)
    return stored == current


# ── Worker session ───────────────────────────────────────────────────

def run_worker_session(number, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    base_state = vc_system.get_current()

    try:
        worker_adapter = adapter_mod.WorkerAdapter(config, number)
        b = backend or create_backend(config)
        result = worker_adapter.run(b)
    except BaseException as exc:
        result = {"status": "error", "reason": str(exc)}

    checked = Observer(workspace, number).task_is_checked()

    if checked:
        termination = "normal" if result.get("status") == "done" else result.get("status", "error")
        try:
            state = vc_system.save_state(
                f"Worker {number}",
                f"T{number} submitted. Termination: {termination}.",
            )
        except BaseException:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": "vc save failed after checked worker"}
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": termination,
            "state": state,
        }

    if base_state:
        try:
            vc_system.restore(base_state)
        except BaseException as exc:
            return {"status": "error", "reason": f"rollback failed: {exc}"}
    return {
        "status": "error",
        "reason": result.get("reason", f"T{number} was not submitted"),
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
    except BaseException as exc:
        # Restore backup
        if ws_exists:
            shutil.rmtree(workspace, ignore_errors=True)
            os.rename(backup_dir, workspace)
        return {"status": "error", "reason": str(exc)}

    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    if result["status"] == "success" and tasks:
        try:
            state = vc_system.save_state("Manager (init)", "Project initialized")
        except BaseException as exc:
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

    while True:
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        status_text = read_file_content(os.path.join(workspace, "docs/project-status.md"))

        if tasks and todo_mod.all_checked(tasks) and _is_completed(workspace, tasks):
            return {"status": "done"}

        unchecked = todo_mod.first_unchecked(tasks)
        if unchecked is not None:
            number = unchecked["number"]
            print(f"Worker {number}...")
            try:
                result = run_worker_session(number, config, backend=backend)
            except BaseException as exc:
                return {"status": "error", "reason": f"Worker {number} exception: {exc}"}
            if result["status"] != "submitted":
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
            print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")
            continue

        print("All submitted. Reviewing artifacts...")
        base_state = vc_system.get_current()

        # Phase 1: review each artifact individually
        reviews = []
        try:
            for task in tasks:
                a_review = adapter_mod.ArtifactReviewAdapter(config, task["number"])
                reviews.append(a_review.run(backend))
        except BaseException as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"review exception: {exc}"}

        errors = [r for r in reviews if r["verdict"] == "ERROR"]
        if errors:
            if base_state:
                vc_system.restore(base_state)
            return {
                "status": "error",
                "reason": f"review errors: {[e.get('reason','?')[:60] for e in errors]}",
                "detail": reviews,
            }

        rework = [r for r in reviews if r["verdict"] == "REWORK"]
        if rework:
            print(f"Reopening {len(rework)} tasks: {[r['task_number'] for r in rework]}")
            todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
            for r_item in rework:
                tn = r_item.get("task_number", 0)
                todo_text = todo_mod.set_task_checked(todo_text, tn, False)
            try:
                write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
                vc_system.save_state("Review", f"reopened {[r_item.get('task_number') for r_item in rework]}")
            except BaseException:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "reopen+save failed"}
            continue

        # Phase 2: all artifacts accepted — check project completeness
        print("All artifacts accepted. Checking completion...")
        try:
            completion = adapter_mod.CompletionReviewAdapter(config)
            c_result = completion.run(backend)
        except BaseException as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review exception: {exc}"}

        if not isinstance(c_result, dict):
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": "completion review returned non-dict"}

        if c_result.get("verdict") == "ERROR":
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review: {c_result.get('reason','?')}"}

        if c_result.get("verdict") == "MISSING":
            missing = _deduplicate(c_result.get("missing", []))
            if not missing:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "MISSING verdict with no items"}
            print(f"Adding {len(missing)} missing tasks...")
            todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
            tasks_now = todo_mod.parse_todo(todo_text)
            max_num = max((t["number"] for t in tasks_now), default=0)
            for desc in missing:
                max_num += 1
                todo_text += f"\n- [ ] T{max_num} — {desc}"
            try:
                write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
                vc_system.save_state("Review", f"added {len(missing)} missing tasks")
            except BaseException:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "missing-add+save failed"}
            continue

        if c_result.get("verdict") == "COMPLETE":
            try:
                tasks_now = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
                plan_hash = _compute_plan_hash(workspace, tasks_now)
                write_file_content(os.path.join(workspace, _COMPLETED_HASH_FILE), plan_hash)
                write_file_content(os.path.join(workspace, "docs/project-status.md"),
                                    "# Project Status\n\nDONE\n")
                vc_system.save_state("Review", "Project completed")
            except BaseException:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "completion+save failed"}
            return {"status": "done"}

        if base_state:
            vc_system.restore(base_state)
        return {"status": "error", "reason": f"unexpected completion verdict: {c_result.get('verdict', '?')}"}


def _deduplicate(items):
    seen = set()
    out = []
    for item in items:
        s = item.strip().lower()
        if s and s not in seen:
            seen.add(s)
            out.append(item.strip())
    return out


def show_status(config):
    workspace = config["workspace"]
    if not os.path.exists(os.path.join(workspace, ".bid")):
        print("No BID project in workspace.")
        return
    current = vc_mod.VersionControl(workspace).get_current() or "?"
    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    checked = sum(1 for task in tasks if task["checked"])
    completed = _is_completed(workspace, tasks)
    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{len(tasks)} checked")
    print(f"Done:     {'yes' if completed else 'no'}")
    for task in tasks:
        mark = "x" if task["checked"] else " "
        print(f"  [{mark}] {task['id']} — {task['description']}")
