import hashlib
import hmac
import json
import os
import re
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
_ACTIVE_SESSION_FILE = ".bid/active-session.json"

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


def _write_json_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _remove_file(path):
    try:
        os.remove(path)
    except OSError:
        pass


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


def _workspace_lock_path(workspace):
    real = os.path.realpath(workspace)
    parent = os.path.dirname(real)
    name = os.path.basename(real) or "workspace"
    identity = hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]
    return os.path.join(parent, f".{name}.bid-run-{identity}.lock")


def _init_journal_path(workspace):
    return _workspace_lock_path(workspace) + ".init.json"


def _read_lock_metadata(path):
    return _read_json(path) or {}


def _run_lock_is_stale(path):
    metadata = _read_lock_metadata(path)
    pid = metadata.get("pid")
    if isinstance(pid, int) and pid > 0:
        return not _pid_is_alive(pid)
    try:
        return time.time() - os.path.getmtime(path) > _RUN_LOCK_STALE
    except OSError:
        return True


def _legacy_run_lock_is_live(workspace):
    path = os.path.join(workspace, ".bid", "run.lock")
    return os.path.exists(path) and not _run_lock_is_stale(path)


def _existing_run_is_live(workspace):
    stable = _workspace_lock_path(workspace)
    return (
        os.path.exists(stable) and not _run_lock_is_stale(stable)
    ) or _legacy_run_lock_is_live(workspace)


@contextmanager
def _project_run_lock(workspace):
    path = _workspace_lock_path(workspace)
    os.makedirs(os.path.dirname(path), exist_ok=True)
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
            if _run_lock_is_stale(path):
                _remove_file(path)
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError("another BID process is already running this workspace")
            time.sleep(0.1)
    try:
        yield
    finally:
        metadata = _read_lock_metadata(path)
        if metadata.get("token") == token:
            _remove_file(path)


def _active_session_path(workspace):
    return os.path.join(workspace, _ACTIVE_SESSION_FILE)


def _begin_active_session(workspace, kind, base_state, task_number=None):
    marker = {
        "kind": kind,
        "base_state": base_state,
        "task_number": task_number,
        "pid": os.getpid(),
        "started": time.time(),
    }
    _write_json_atomic(_active_session_path(workspace), marker)


def _clear_active_session(workspace):
    _remove_file(_active_session_path(workspace))


def _recover_active_session(workspace):
    path = _active_session_path(workspace)
    marker = _read_json(path)
    if marker is None:
        if os.path.exists(path):
            return {"status": "error", "reason": "active-session journal is corrupt"}
        return {"status": "none"}

    system = vc_mod.VersionControl(workspace)
    base_state = marker.get("base_state")
    current_state = system.get_current()
    if base_state and current_state and current_state != base_state:
        _clear_active_session(workspace)
        return {"status": "already-committed", "state": current_state}

    kind = marker.get("kind")
    if kind == "worker":
        number = marker.get("task_number")
        if not isinstance(number, int) or number <= 0:
            return {"status": "error", "reason": "active Worker journal has invalid task number"}
        if Observer(workspace, number).task_is_checked():
            try:
                state = system.save_state(
                    f"Worker {number}",
                    f"T{number} recovered after process interruption.",
                )
            except Exception as exc:
                return {"status": "error", "reason": f"failed to commit recovered Worker: {exc}"}
            _clear_active_session(workspace)
            return {"status": "submitted", "task_number": number, "state": state}

    if base_state:
        try:
            system.restore(base_state)
        except Exception as exc:
            return {"status": "error", "reason": f"failed to restore interrupted session: {exc}"}
    _clear_active_session(workspace)
    return {"status": "rolled-back", "kind": kind}


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
                if not re.fullmatch(r"search-\d+\.md", name):
                    continue
                evidence = os.path.join(research_dir, name)
                if not os.path.isfile(evidence):
                    continue
                digest.update(f"RESEARCH:T{task['number']}/{name}\0".encode("utf-8"))
                with open(evidence, "rb") as handle:
                    digest.update(handle.read())
    return digest.hexdigest()


def _is_completed(workspace, tasks):
    status = read_file_content(os.path.join(workspace, "docs/project-status.md")).strip()
    if status != "# Project Status\n\nDONE":
        return False
    hash_path = os.path.join(workspace, _COMPLETED_HASH_FILE)
    if not os.path.isfile(hash_path):
        return False
    return hmac.compare_digest(
        read_file_content(hash_path).strip(),
        _compute_plan_hash(workspace, tasks),
    )


def run_worker_session(number, config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    recovery = _recover_active_session(workspace)
    if recovery.get("status") == "error":
        return recovery

    system = vc_mod.VersionControl(workspace)
    base_state = system.get_current()
    _begin_active_session(workspace, "worker", base_state, number)
    interrupted = False

    try:
        result = adapter_mod.WorkerAdapter(config, number).run(backend or create_backend(config))
    except KeyboardInterrupt:
        interrupted = True
        result = {"status": "interrupted", "reason": "interrupted by user"}
    except Exception as exc:
        result = {"status": "error", "reason": str(exc)}

    checked = Observer(workspace, number).task_is_checked()
    if checked:
        termination = "normal" if result.get("status") == "done" else result.get("status", "error")
        try:
            state = system.save_state(
                f"Worker {number}",
                f"T{number} submitted. Termination: {termination}.",
            )
        except Exception as exc:
            if base_state:
                system.restore(base_state)
            _clear_active_session(workspace)
            return {"status": "error", "reason": f"vc save failed after checked worker: {exc}"}
        _clear_active_session(workspace)
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": termination,
            "state": state,
            "interrupted": interrupted,
        }

    if base_state:
        try:
            system.restore(base_state)
        except Exception as exc:
            return {"status": "error", "reason": f"rollback failed: {exc}"}
    _clear_active_session(workspace)
    if interrupted:
        return {"status": "interrupted", "reason": "interrupted; unchecked work rolled back"}
    return {"status": "error", "reason": result.get("reason", f"T{number} was not submitted")}


def _recover_init_journal(workspace):
    path = _init_journal_path(workspace)
    journal = _read_json(path)
    if journal is None:
        if os.path.exists(path):
            return {"status": "error", "reason": "initialization journal is corrupt"}
        return {"status": "none"}

    phase = journal.get("phase")
    backup = journal.get("backup")
    existed = bool(journal.get("workspace_existed"))
    if backup and os.path.exists(backup):
        backup = os.path.realpath(backup)
    else:
        backup = None

    if phase == "committed":
        if backup:
            shutil.rmtree(backup, ignore_errors=True)
        _remove_file(path)
        return {"status": "kept-new"}

    if phase == "planned" and existed and backup is None:
        _remove_file(path)
        return {"status": "old-untouched"}

    shutil.rmtree(workspace, ignore_errors=True)
    if existed and backup:
        os.rename(backup, workspace)
    _remove_file(path)
    return {"status": "restored-old" if existed else "removed-partial"}


def init_project(user_task, config, backend=None):
    workspace = os.path.realpath(config["workspace"])
    if _legacy_run_lock_is_live(workspace):
        return {"status": "error", "reason": "cannot initialize while BID is running this workspace"}
    try:
        with _project_run_lock(workspace):
            recovered = _recover_init_journal(workspace)
            if recovered.get("status") == "error":
                return recovered
            return _init_project_locked(user_task, config, backend)
    except RuntimeError as exc:
        return {"status": "error", "reason": str(exc)}


def _init_project_locked(user_task, config, backend=None):
    workspace = os.path.realpath(config["workspace"])
    runtime_config = dict(config)
    runtime_config["workspace"] = workspace
    parent = os.path.dirname(workspace)
    os.makedirs(parent, exist_ok=True)
    existed = os.path.exists(workspace)
    backup = tempfile.mkdtemp(prefix=f".{os.path.basename(workspace)}-backup-", dir=parent)
    os.rmdir(backup)
    journal_path = _init_journal_path(workspace)
    _write_json_atomic(journal_path, {
        "phase": "planned",
        "workspace": workspace,
        "workspace_existed": existed,
        "backup": backup,
    })

    try:
        if existed:
            os.rename(workspace, backup)
        _write_json_atomic(journal_path, {
            "phase": "backed-up",
            "workspace": workspace,
            "workspace_existed": existed,
            "backup": backup,
        })

        os.makedirs(workspace, exist_ok=True)
        ensure_workspace(workspace)
        write_file_content(os.path.join(workspace, "docs/task.md"), f"# Task\n\n{user_task}\n")
        write_file_content(os.path.join(workspace, "docs/project-status.md"), "# Project Status\n\nInitialized.\n")
        write_file_content(os.path.join(workspace, "docs/decisions.md"), "# Decisions\n\n")

        system = vc_mod.VersionControl(workspace)
        system.init()
        result = adapter_mod.ManagerInitAdapter(runtime_config).run(backend or create_backend(runtime_config))
        tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
        if result.get("status") != "success" or not tasks:
            raise RuntimeError(result.get("reason", "Manager did not create a valid TODO"))
        state = system.save_state("Manager (init)", "Project initialized")
        _write_json_atomic(journal_path, {
            "phase": "committed",
            "workspace": workspace,
            "workspace_existed": existed,
            "backup": backup,
        })
    except BaseException as exc:
        _recover_init_journal(workspace)
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"status": "error", "reason": str(exc)}

    if existed and os.path.exists(backup):
        shutil.rmtree(backup, ignore_errors=True)
    _remove_file(journal_path)
    return {"status": "success", "state": state}


def run_project(config, backend=None):
    workspace = os.path.realpath(config["workspace"])
    runtime_config = dict(config)
    runtime_config["workspace"] = workspace
    with _project_run_lock(workspace):
        recovered_init = _recover_init_journal(workspace)
        if recovered_init.get("status") == "error":
            return recovered_init
        if not os.path.isdir(os.path.join(workspace, ".bid")):
            return {"status": "error", "reason": "no BID project found"}
        ensure_workspace(workspace)
        recovered = _recover_active_session(workspace)
        if recovered.get("status") == "error":
            return recovered
        return _run_project_locked(runtime_config, backend or create_backend(runtime_config))


def _restore_and_clear(system, base_state, workspace):
    if base_state:
        system.restore(base_state)
    _clear_active_session(workspace)


def _run_project_locked(config, backend):
    workspace = config["workspace"]
    system = vc_mod.VersionControl(workspace)

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
        base_state = system.get_current()
        _begin_active_session(workspace, "review", base_state)
        reviews = []
        try:
            for task in tasks:
                reviews.append(adapter_mod.ArtifactReviewAdapter(config, task["number"]).run(backend))
        except KeyboardInterrupt:
            _restore_and_clear(system, base_state, workspace)
            return {"status": "paused", "reason": "interrupted during artifact review"}
        except Exception as exc:
            _restore_and_clear(system, base_state, workspace)
            return {"status": "error", "reason": f"review exception: {exc}"}

        errors = [review for review in reviews if review.get("verdict") == "ERROR"]
        if errors:
            _restore_and_clear(system, base_state, workspace)
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
                system.save_state("Review", f"reopened {[item['task_number'] for item in rework]}")
            except Exception as exc:
                _restore_and_clear(system, base_state, workspace)
                return {"status": "error", "reason": f"reopen+save failed: {exc}"}
            _clear_active_session(workspace)
            continue

        print("All artifacts accepted. Checking completion...")
        try:
            completion = adapter_mod.CompletionReviewAdapter(config).run(backend)
        except KeyboardInterrupt:
            _restore_and_clear(system, base_state, workspace)
            return {"status": "paused", "reason": "interrupted during completion review"}
        except Exception as exc:
            _restore_and_clear(system, base_state, workspace)
            return {"status": "error", "reason": f"completion review exception: {exc}"}

        if not isinstance(completion, dict):
            _restore_and_clear(system, base_state, workspace)
            return {"status": "error", "reason": "completion review returned non-dict"}
        verdict = completion.get("verdict")
        if verdict == "ERROR":
            _restore_and_clear(system, base_state, workspace)
            return {"status": "error", "reason": f"completion review: {completion.get('reason', '?')}"}

        if verdict == "MISSING":
            missing = _deduplicate(completion.get("missing", []))
            if not missing:
                _restore_and_clear(system, base_state, workspace)
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
                system.save_state("Review", f"added {len(missing)} missing tasks")
            except Exception as exc:
                _restore_and_clear(system, base_state, workspace)
                return {"status": "error", "reason": f"missing-add+save failed: {exc}"}
            _clear_active_session(workspace)
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
                system.save_state("Review", "Project completed")
            except Exception as exc:
                _restore_and_clear(system, base_state, workspace)
                return {"status": "error", "reason": f"completion+save failed: {exc}"}
            _clear_active_session(workspace)
            return {"status": "done"}

        _restore_and_clear(system, base_state, workspace)
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
