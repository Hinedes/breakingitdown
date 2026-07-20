import os

from . import model as model_mod
from . import permissions
from . import session
from . import todo as todo_mod
from . import tools as tools_mod
from . import vc as vc_mod
from .observer import Observer


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

When your current Manager job is complete, output exactly `Done`.
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


def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    manager_path = os.path.join(docs, "manager.md")
    worker_path = os.path.join(docs, "worker.md")
    if not os.path.exists(manager_path):
        write_file_content(manager_path, MANAGER_INSTRUCTIONS)
    if not os.path.exists(worker_path):
        write_file_content(worker_path, WORKER_INSTRUCTIONS)


def run_agent(messages, tools, backend, config, workspace, role, worker_number=None):
    hard_ceiling = config.get("worker_timeout", 3600)
    inactivity_timeout = config.get("inactivity_timeout", 600)
    repeat_limit = config.get("repeat_action_limit", 5)
    observer = Observer(workspace, worker_number or 0)
    done_without_check = 0

    while observer.elapsed() < hard_ceiling:
        try:
            result = session.run_turn(
                messages,
                tools,
                backend,
                config,
                workspace,
                role,
                worker_number,
            )
        except Exception as exc:
            return {
                "status": "error",
                "reason": f"model request failed: {exc}",
                "messages": messages,
            }

        changed_files = observer.poll_changes()
        repeated = 0
        for event in result["tool_events"]:
            repeated = max(
                repeated,
                observer.record_action(event["name"], event["arguments"], event["result"]),
            )
            if event["success"]:
                observer.mark_activity()

        content = result["content"]
        if content:
            observer.mark_activity()

        if repeated >= repeat_limit and not changed_files:
            return {
                "status": "stalled",
                "reason": f"same operation repeated {repeated} times without project change",
                "messages": messages,
            }

        if observer.seen_done(content):
            if role == permissions.ROLE_WORKER and not observer.task_is_checked():
                done_without_check += 1
                if done_without_check >= repeat_limit:
                    return {
                        "status": "stalled",
                        "reason": f"Worker {worker_number} repeatedly ended without submitting T{worker_number}",
                        "messages": messages,
                    }
                messages.append({
                    "role": "user",
                    "content": (
                        f"T{worker_number} is still unchecked. Continue working. "
                        f"Submit by rewriting docs/todo.md and changing only T{worker_number} to [x]."
                    ),
                })
                continue
            return {"status": "done", "messages": messages}

        if observer.inactive_for() > inactivity_timeout:
            return {
                "status": "timeout",
                "reason": f"inactive for {observer.inactive_for():.0f}s",
                "messages": messages,
            }

    return {
        "status": "timeout",
        "reason": f"hard ceiling reached after {hard_ceiling}s",
        "messages": messages,
    }


def run_manager_init(config, backend=None):
    workspace = config["workspace"]
    messages = [
        {"role": "system", "content": load_prompt("manager")},
        {
            "role": "user",
            "content": (
                "Read docs/manager.md and docs/task.md. Initialize docs/todo.md "
                "with numbered sequential tasks. Then output exactly Done."
            ),
        },
    ]
    tools = tools_mod.get_tools_for_role(permissions.ROLE_MANAGER)
    return run_agent(
        messages,
        tools,
        backend or create_backend(config),
        config,
        workspace,
        permissions.ROLE_MANAGER,
    )


def run_manager_review(config, backend=None):
    workspace = config["workspace"]
    messages = [
        {"role": "system", "content": load_prompt("manager")},
        {
            "role": "user",
            "content": (
                "Read docs/manager.md, docs/task.md, docs/todo.md, and the relevant "
                "Worker artifacts. Accept, reopen, repair, or extend the work. "
                "Then output exactly Done."
            ),
        },
    ]
    tools = tools_mod.get_tools_for_role(permissions.ROLE_MANAGER)
    return run_agent(
        messages,
        tools,
        backend or create_backend(config),
        config,
        workspace,
        permissions.ROLE_MANAGER,
    )


def run_worker_session(number, config, backend=None):
    workspace = config["workspace"]
    vc_system = vc_mod.VersionControl(workspace)
    base_state = vc_system.get_current()
    messages = [
        {"role": "system", "content": f"/no_think\nBID Worker {number}."},
        {
            "role": "user",
            "content": (
                f"Read docs/worker.md and docs/todo.md. Perform only Task T{number}. "
                f"When the final submitted state is ready, keep T{number} checked and output exactly Done."
            ),
        },
    ]
    tools = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=number)
    result = run_agent(
        messages,
        tools,
        backend or create_backend(config),
        config,
        workspace,
        permissions.ROLE_WORKER,
        number,
    )

    checked = Observer(workspace, number).task_is_checked()
    if checked:
        termination = "normal" if result["status"] == "done" else result["status"]
        state = vc_system.save_state(
            f"Worker {number}",
            f"T{number} submitted. Termination: {termination}.",
        )
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": termination,
            "state": state,
        }

    if base_state:
        vc_system.restore(base_state)
    return {
        "status": "error",
        "reason": result.get("reason", f"T{number} was not submitted"),
    }


def init_project(user_task, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    write_file_content(os.path.join(workspace, "docs/task.md"), f"# Task\n\n{user_task}\n")
    write_file_content(os.path.join(workspace, "docs/todo.md"), "# TODO\n\n")
    write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
    write_file_content(os.path.join(workspace, "docs/decisions.md"), "# Decisions\n\n")

    vc_system = vc_mod.VersionControl(workspace)
    vc_system.init()
    result = run_manager_init(config, backend=backend)
    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    if result["status"] == "done" and tasks:
        state = vc_system.save_state("Manager (init)", "Project initialized")
        return {"status": "success", "state": state}

    vc_system.restore("s0")
    return {
        "status": "error",
        "reason": result.get("reason", "Manager did not create a valid TODO"),
    }


def run_project(config, backend=None):
    workspace = config["workspace"]
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
            result = run_worker_session(number, config, backend=backend)
            if result["status"] != "submitted":
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
            print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")
            continue

        if tasks and todo_mod.all_checked(tasks) and "DONE" in status_text:
            return {"status": "done"}

        print("All tasks submitted. Running Manager review...")
        base_state = vc_system.get_current()
        result = run_manager_review(config, backend=backend)
        if result["status"] != "done":
            if base_state:
                vc_system.restore(base_state)
            return {
                "status": "error",
                "reason": "Manager review failed",
                "detail": result,
            }

        review_state = vc_system.save_state("Manager (review)", "Review completed")
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        status_text = read_file_content(os.path.join(workspace, "docs/project-status.md"))

        if tasks and todo_mod.all_checked(tasks) and "DONE" in status_text:
            return {"status": "done", "state": review_state}
        if todo_mod.first_unchecked(tasks) is not None:
            print("Manager reopened or added work. Continuing...")
            continue

        print("Manager produced no executable next state. Pausing.")
        return {"status": "paused", "state": review_state}


def show_status(config):
    workspace = config["workspace"]
    if not os.path.exists(os.path.join(workspace, ".bid")):
        print("No BID project in workspace.")
        return
    vc_system = vc_mod.VersionControl(workspace)
    current = vc_system.get_current() or "?"
    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    checked = sum(1 for task in tasks if task["checked"])
    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{len(tasks)} checked")
    for task in tasks:
        mark = "x" if task["checked"] else " "
        print(f"  [{mark}] {task['id']} — {task['description']}")
