import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager

from . import adapter as adapter_mod
from . import model as model_mod
from . import permissions
from . import todo as todo_mod
from . import vc as vc_mod
from .observer import Observer


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")
_RUN_LOCK_TIMEOUT = 30.0
_RUN_LOCK_STALE = 120.0

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


def _policy_content(name):
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    if name == "manager":
        return MANAGER_INSTRUCTIONS
    if name == "worker":
        return WORKER_INSTRUCTIONS
    return ""


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
    return _policy_content(name)


def read_file_content(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def write_file_content(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    write_file_content(os.path.join(docs, "manager.md"), _policy_content("manager"))
    write_file_content(os.path.join(docs, "worker.md"), _policy_content("worker"))


def _pid_is_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _project_run_lock(workspace):
    bid_dir = os.path.join(workspace, ".bid")
    os.makedirs(bid_dir, exist_ok=True)
    path = os.path.join(bid_dir, "run.lock")
    deadline = time.monotonic() + _RUN_LOCK_TIMEOUT
    token = f"{os.getpid()}-{time.time_ns()}"
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created": time.time(), "token": token}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            break
        except FileExistsError:
            stale = False
            try:
                age = time.time() - os.path.getmtime(path)
                with open(path, encoding="utf-8") as handle:
                    metadata = json.load(handle)
                stale = age > _RUN_LOCK_STALE or not _pid_is_alive(metadata.get("pid"))
            except (OSError, json.JSONDecodeError):
                stale = True
            if stale:
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("another BID process is already running this workspace")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            with open(path, encoding="utf-8") as handle:
                metadata = json.load(handle)
            if metadata.get("token") == token:
                os.remove(path)
        except (OSError, json.JSONDecodeError):
            pass


def _hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _safe_workspace_file(workspace, rel_path):
    safe, _, normalized = permissions.check_path_safety(rel_path, workspace)
    if not safe or normalized in (None, "."):
        return None, None
    absolute = os.path.realpath(os.path.join(workspace, normalized))
    root = os.path.realpath(workspace)
    if not absolute.startswith(root + os.sep):
        return None, None
    return normalized, absolute


_COMPLETED_HASH_FILE = "docs/.completed_hash"


def _compute_plan_hash(workspace, tasks):
    """Hash TODO, declared artifacts, and task-scoped research evidence."""
    digest = hashlib.sha256()
    todo_path = os.path.join(workspace, "docs", "todo.md")
    try:
        with open(todo_path, "rb") as handle:
            digest.update(b"TODO\0" + handle.read())
    except OSError:
        digest.update(b"TODO\0<MISSING>")

    for task in sorted(tasks, key=lambda item: item["number"]):
        output, _ = todo_mod.get_task_metadata(tasks, task["number"])
        normalized, absolute = _safe_workspace_file(workspace, output)
        digest.update(f"TASK:{task['number']}\0OUTPUT:{normalized or '<INVALID>'}\0".encode("utf-8"))
        if absolute and os.path.isfile(absolute):
            with open(absolute, "rb") as handle:
                digest.update(handle.read())
        else:
            digest.update(b"<MISSING>")

        research_dir = os.path.join(workspace, "docs", "research", f"T{task['number']}")
        if os.path.isdir(research_dir):
            for name in sorted(os.listdir(research_dir)):
                if not name.startswith("search-") or not name.endswith(".md"):
                    continue
                path = os.path.join(research_dir, name)
                if not os.path.isfile(path):
                    continue
                digest.update(f"RESEARCH:T{task['number']}/{name}\0".encode("utf-8"))
                with open(path, "rb") as handle:
                    digest.update(handle.read())
    return digest.hexdigest()


def _is_completed(workspace, tasks):
    status = read_file_content(os.path.join(workspace, "docs/project-status.md")).strip()
    if status != "# Project Status\n\nDONE":
        return False
    hash_path = os.path.join(workspace, _COMPLETED_HASH_FILE)
    if not os.path.isfile(hash_path):
        return False
    stored = read_file_content(hash_path).strip()
    return hmac.compare_digest(stored, _compute_plan_hash(workspace, tasks))


def run_worker_session(number, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    base_state = vc_system.get_current()
    interrupted = False

    try:
        worker_adapter = adapter_mod.WorkerAdapter(config, number)
        result = worker_adapter.run(backend or create_backend(config))
    except KeyboardInterrupt:
        interrupted = True
        result = {"status": "interrupted", "reason": "interrupted by user"}
    except Exception as exc:
        result = {"status": "error", "reason": str(exc)}

    checked = Observer(workspace, number).task_is_checked()
    if checked:
        termination = "normal" if result.get("status") == "done" else result.get("status", "error")
        try:
            state = vc_system.save_state(
                f"Worker {number}",
                f"T{number} submitted. Termination: {termination}.",
            )
        except Exception as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"vc save failed after checked worker: {exc}"}
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": termination,
            "state": state,
            "interrupted": interrupted,
        }

    if base_state:
        try:
            vc_system.restore(base_state)
        except Exception as exc:
            return {"status": "error", "reason": f"rollback failed: {exc}"}
    if interrupted:
        return {"status": "interrupted", "reason": "interrupted; unchecked work rolled back"}
    return {"status": "error", "reason": result.get("reason", f"T{number} was not submitted")}


def init_project(user_task, config, backend=None):
    workspace = os.path.realpath(config["workspace"])
    parent = os.path.dirname(workspace)
    os.makedirs(parent, exist_ok=True)
    existed = os.path.exists(workspace)
    backup = None

    if existed:
        backup = tempfile.mkdtemp(prefix=f".{os.path.basename(workspace)}-backup-", dir=parent)
        os.rmdir(backup)
        os.rename(workspace, backup)

    try:
        os.makedirs(workspace, exist_ok=True)
        ensure_workspace(workspace)
        write_file_content(os.path.join(workspace, "docs/task.md"), f"# Task\n\n{user_task}\n")
        write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
        write_file_content(os.path.join(workspace, "docs/decisions.md"), "# Decisions\n\n")

        vc_system = vc_mod.VersionControl(workspace)
        vc_system.init()
        result = adapter_mod.ManagerInitAdapter(config).run(backend or create_backend(config))
        tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
        if result.get("status") != "success" or not tasks:
            raise RuntimeError(result.get("reason", "Manager did not create a valid TODO"))
        state = vc_system.save_state("Manager (init)", "Project initialized")
    except BaseException as exc:
        shutil.rmtree(workspace, ignore_errors=True)
        if backup and os.path.exists(backup):
            os.rename(backup, workspace)
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"status": "error", "reason": str(exc)}

    if backup and os.path.exists(backup):
        shutil.rmtree(backup, ignore_errors=True)
    return {"status": "success", "state": state}


def run_project(config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    with _project_run_lock(workspace):
        return _run_project_locked(config, backend or create_backend(config))


def _run_project_locked(config, backend):
    workspace = config["workspace"]
    vc_system = vc_mod.VersionControl(workspace)

    while True:
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)
        if tasks and todo_mod.all_checked(tasks) and _is_completed(workspace, tasks):
            return {"status": "done"}

        unchecked = todo_mod.first_unchecked(tasks)
        if unchecked is not None:
            number = unchecked["number"]
            print(f"Worker {number}...")
            result = run_worker_session(number, config, backend=backend)
            if result.get("status") == "interrupted":
                return {"status": "paused", "reason": result.get("reason")}
            if result.get("status") != "submitted":
                print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
                return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
            print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")
            if result.get("interrupted"):
                return {"status": "paused", "state": result["state"], "reason": "interrupted after submission"}
            continue

        print("All submitted. Reviewing artifacts...")
        base_state = vc_system.get_current()
        reviews = []
        try:
            for task in tasks:
                reviews.append(adapter_mod.ArtifactReviewAdapter(config, task["number"]).run(backend))
        except KeyboardInterrupt:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "paused", "reason": "interrupted during artifact review"}
        except Exception as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"review exception: {exc}"}

        errors = [review for review in reviews if review.get("verdict") == "ERROR"]
        if errors:
            if base_state:
                vc_system.restore(base_state)
            return {
                "status": "error",
                "reason": f"review errors: {[item.get('reason', '?')[:60] for item in errors]}",
                "detail": reviews,
            }

        rework = [review for review in reviews if review.get("verdict") == "REWORK"]
        if rework:
            print(f"Reopening {len(rework)} tasks: {[item['task_number'] for item in rework]}")
            updated = read_file_content(os.path.join(workspace, "docs/todo.md"))
            for item in rework:
                updated = todo_mod.set_task_checked(updated, item["task_number"], False)
            try:
                write_file_content(os.path.join(workspace, "docs/todo.md"), updated)
                vc_system.save_state("Review", f"reopened {[item['task_number'] for item in rework]}")
            except Exception as exc:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": f"reopen+save failed: {exc}"}
            continue

        print("All artifacts accepted. Checking completion...")
        try:
            completion = adapter_mod.CompletionReviewAdapter(config).run(backend)
        except KeyboardInterrupt:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "paused", "reason": "interrupted during completion review"}
        except Exception as exc:
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review exception: {exc}"}

        if not isinstance(completion, dict):
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": "completion review returned non-dict"}
        verdict = completion.get("verdict")
        if verdict == "ERROR":
            if base_state:
                vc_system.restore(base_state)
            return {"status": "error", "reason": f"completion review: {completion.get('reason', '?')}"}

        if verdict == "MISSING":
            missing = _deduplicate(completion.get("missing", []))
            if not missing:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": "MISSING verdict with no items"}
            print(f"Adding {len(missing)} missing tasks...")
            updated = read_file_content(os.path.join(workspace, "docs/todo.md"))
            current_tasks = todo_mod.parse_todo(updated)
            number = max((task["number"] for task in current_tasks), default=0)
            for description in missing:
                number += 1
                updated += f"\n- [ ] T{number} — {description}"
            try:
                write_file_content(os.path.join(workspace, "docs/todo.md"), updated)
                vc_system.save_state("Review", f"added {len(missing)} missing tasks")
            except Exception as exc:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": f"missing-add+save failed: {exc}"}
            continue

        if verdict == "COMPLETE":
            try:
                current_tasks = todo_mod.parse_todo(
                    read_file_content(os.path.join(workspace, "docs/todo.md"))
                )
                write_file_content(
                    os.path.join(workspace, _COMPLETED_HASH_FILE),
                    _compute_plan_hash(workspace, current_tasks),
                )
                write_file_content(
                    os.path.join(workspace, "docs/project-status.md"),
                    "# Project Status\n\nDONE\n",
                )
                vc_system.save_state("Review", "Project completed")
            except Exception as exc:
                if base_state:
                    vc_system.restore(base_state)
                return {"status": "error", "reason": f"completion+save failed: {exc}"}
            return {"status": "done"}

        if base_state:
            vc_system.restore(base_state)
        return {"status": "error", "reason": f"unexpected completion verdict: {verdict or '?'}"}


def _deduplicate(items):
    seen = set()
    output = []
    for item in items:
        value = str(item).strip()
        key = value.lower()
        if key and key not in seen:
            seen.add(key)
            output.append(value)
    return output


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
    print(f"Done:     {'yes' if _is_completed(workspace, tasks) else 'no'}")
    for task in tasks:
        marker = "x" if task["checked"] else " "
        print(f"  [{marker}] {task['id']} — {task['description']}")
