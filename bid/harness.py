import os

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


def run_worker_session(number, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    base_state = vc_system.get_current()
    worker_adapter = adapter_mod.WorkerAdapter(config, number)
    backend = backend or create_backend(config)
    try:
        result = worker_adapter.run(backend)
    except Exception as exc:
        result = {"status": "error", "reason": f"adapter exception: {exc}"}

    checked = Observer(workspace, number).task_is_checked()
    if checked:
        termination = "normal" if result["status"] == "done" else result["status"]
        try:
            state = vc_system.save_state(
                f"Worker {number}",
                f"T{number} submitted. Termination: {termination}.",
            )
        except Exception as exc:
            return {"status": "error", "reason": f"vc save failed: {exc}"}
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": termination,
            "state": state,
        }

    if base_state:
        try:
            vc_system.restore(base_state)
        except Exception as exc:
            return {"status": "error", "reason": f"vc rollback failed: {exc}"}
    return {
        "status": "error",
        "reason": result.get("reason", f"T{number} was not submitted"),
    }


def init_project(user_task, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    write_file_content(os.path.join(workspace, "docs/task.md"), f"# Task\n\n{user_task}\n")
    write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
    write_file_content(os.path.join(workspace, "docs/decisions.md"), "# Decisions\n\n")

    vc_system = vc_mod.VersionControl(workspace)
    vc_system.init()
    adapter = adapter_mod.ManagerInitAdapter(config)
    backend = backend or create_backend(config)
    try:
        result = adapter.run(backend)
    except Exception as exc:
        vc_system.restore("s0")
        return {"status": "error", "reason": f"init adapter exception: {exc}"}

    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    if result["status"] == "success" and tasks:
        try:
            state = vc_system.save_state("Manager (init)", "Project initialized")
        except Exception as exc:
            vc_system.restore("s0")
            return {"status": "error", "reason": f"vc save failed: {exc}"}
        return {"status": "success", "state": state}

    vc_system.restore("s0")
    return {
        "status": "error",
        "reason": result.get("reason", "Manager did not create a valid TODO"),
    }


def run_project(config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    backend = backend or create_backend(config)

    while True:
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        status_text = read_file_content(os.path.join(workspace, "docs/project-status.md"))

        unchecked = todo_mod.first_unchecked(tasks)
        if unchecked is not None:
            number = unchecked["number"]
            print(f"Worker {number}...")
            try:
                result = run_worker_session(number, config, backend=backend)
            except Exception as exc:
                return {"status": "error", "reason": f"Worker {number} exception: {exc}"}
            if result["status"] != "submitted":
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
            print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")
            continue

        if tasks and todo_mod.all_checked(tasks) and "DONE" in status_text:
            return {"status": "done"}

        print("All submitted. Reviewing artifacts...")
        base_state = vc_system.get_current()

        # Phase 1: review each artifact individually
        reviews = []
        try:
            for task in tasks:
                a_review = adapter_mod.ArtifactReviewAdapter(config, task["number"])
                r = a_review.run(backend)
                reviews.append(r)
        except Exception as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"review exception: {exc}"}

        # ERROR verdict means the review itself failed, not the artifact
        errors = [r for r in reviews if r["verdict"] == "ERROR"]
        if errors:
            print(f"Review phase had {len(errors)} errors. Pausing.")
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
            for r in rework:
                tn = r.get("task_number", 0)
                todo_text = todo_mod.set_task_checked(todo_text, tn, False)
            write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
            vc_system.save_state("Review", f"reopened {[r.get('task_number') for r in rework]}")
            continue

        # Phase 2: all artifacts accepted — check project completeness
        print("All artifacts accepted. Checking completion...")
        try:
            completion = adapter_mod.CompletionReviewAdapter(config)
            c_result = completion.run(backend)
        except Exception as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review exception: {exc}"}

        if c_result["verdict"] == "ERROR":
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review: {c_result.get('reason','?')}"}

        if c_result["verdict"] == "MISSING":
            missing = c_result.get("missing", [])
            if not missing:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "MISSING verdict with no items"}
            print(f"Adding {len(missing)} missing tasks...")
            todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
            tasks_now = todo_mod.parse_todo(todo_text)
            max_num = max((t["number"] for t in tasks_now), default=0)
            last_task = adapter_mod._find_last_task_line(todo_text)
            for desc in missing:
                max_num += 1
                line = f"- [ ] T{max_num} — {desc}"
                if last_task >= 0:
                    lines = todo_text.split("\n")
                    lines.insert(last_task + 1, line)
                    todo_text = "\n".join(lines)
                else:
                    todo_text += "\n" + line
                last_task += 1
            write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
            vc_system.save_state("Review", f"added {len(missing)} missing tasks")
            continue

        if c_result["verdict"] == "COMPLETE":
            write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nDONE\n")
            vc_system.save_state("Review", "Project completed")
            return {"status": "done"}

        if base_state:
            vc_system.restore(base_state)
        return {"status": "error", "reason": f"unexpected completion verdict: {c_result.get('verdict', '?')}"}


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
    for task in tasks:
        mark = "x" if task["checked"] else " "
        print(f"  [{mark}] {task['id']} — {task['description']}")
