import re
import os
import shutil

from . import adapter as adapter_mod
from . import model as model_mod
from . import todo as todo_mod
from . import vc as vc_mod
from .observability import get_log


PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


def get_config():
    return {
        "endpoint": os.environ.get("BID_MODEL_ENDPOINT", "http://127.0.0.1:8080/v1/chat/completions"),
        "model_name": os.environ.get("BID_MODEL_NAME", "smollm3-3b"),
        "max_tokens": int(os.environ.get("BID_MAX_TOKENS", "32768")),
        "workspace": os.environ.get("BID_WORKSPACE", os.path.join(os.getcwd(), "workspace")),
        "request_timeout": int(os.environ.get("BID_REQUEST_TIMEOUT", "300")),
        "inactivity_timeout": int(os.environ.get("BID_INACTIVITY_TIMEOUT", "600")),
        "worker_timeout": int(os.environ.get("BID_WORKER_TIMEOUT", "3600")),
        "repeat_action_limit": int(os.environ.get("BID_REPEAT_ACTION_LIMIT", "5")),
        "run_timeout": int(os.environ.get("BID_RUN_TIMEOUT", "60")),
        "max_searches_per_worker": int(os.environ.get("BID_MAX_SEARCHES", "10")),
        "search_endpoint": os.environ.get("BID_SEARCH_ENDPOINT", ""),
        "provisional_batch": int(os.environ.get("BID_PROVISIONAL_BATCH", "4")),
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


def _task_base_state(vc_system, task_number=None):
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
            if task_number:
                task_match = re.search(r"T(\d+)\s", stripped)
                if task_match and int(task_match.group(1)) != task_number:
                    continue  # belongs to a different task, ignore
            return match.group(1)
    return current


def _task_rework_reason(vc_system):
    current = vc_system.get_current()
    if not current:
        return None
    log = vc_system.get_log()
    marker = f"### {current}\n"
    start = log.rfind(marker)
    if start < 0:
        return None
    section = log[start + len(marker):]
    reason = None
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## ") or stripped.startswith("### "):
            break
        match = re.search(r"\brework_reason:\s*(.*)", stripped)
        if match:
            raw = match.group(1).strip() or ""
            reason_match = re.search(r"\breason=(\S.*)", raw)
            if reason_match:
                reason = reason_match.group(1).strip()
            else:
                reason = raw or None
    return reason


# ── Provisional submission records ───────────────────────────────────
#
# The TODO marker `[-]` is the human-readable state. The authoritative
# technical record lives in the append-only VC log:
#
#   provisional: task=Tn base=sX candidate=sY review=ACCEPT
#   ratified:    task=Tn base=sX candidate=sY
#   invalidate:  task=Tn base=sX candidate=sY reason=rework-suffix
#
# The active record for a task is the latest provisional record not
# superseded by a later ratified or invalidate record for the same task.

_PROVISIONAL_RE = re.compile(
    r"^provisional: task=(T\d+) base=(s\d+) candidate=(s\d+) review=(\S+)\s*$"
)
_RATIFIED_RE = re.compile(
    r"^ratified: task=(T\d+) base=(s\d+) candidate=(s\d+)\s*$"
)
_INVALIDATE_RE = re.compile(
    r"^invalidate: task=(T\d+) base=(s\d+) candidate=(s\d+) reason=(\S.*?)\s*$"
)


def _provisional_records(vc_system):
    """Parse the VC log into {task_number: {"base": sX, "candidate": sY}}.

    The latest provisional record per task wins unless a later ratified or
    invalidate record supersedes it. Superseded records are ignored.
    """
    log = vc_system.get_log()
    events = {}  # task_number -> list of (kind, base, candidate)
    for line in log.splitlines():
        stripped = line.strip()
        match = _PROVISIONAL_RE.match(stripped)
        if match:
            number = int(match.group(1)[1:])
            events.setdefault(number, []).append(
                ("provisional", match.group(2), match.group(3))
            )
            continue
        match = _RATIFIED_RE.match(stripped)
        if match:
            number = int(match.group(1)[1:])
            events.setdefault(number, []).append(
                ("ratified", match.group(2), match.group(3))
            )
            continue
        match = _INVALIDATE_RE.match(stripped)
        if match:
            number = int(match.group(1)[1:])
            events.setdefault(number, []).append(
                ("invalidate", match.group(2), match.group(3))
            )
            continue

    active = {}
    for number, event_list in events.items():
        latest = None
        for kind, base, candidate in event_list:
            if kind == "provisional":
                latest = (base, candidate)
            else:
                latest = None
        if latest is not None:
            active[number] = {"base": latest[0], "candidate": latest[1]}
    return active


def _snapshot_exists(vc_system, state_name):
    if not state_name:
        return False
    return os.path.isdir(os.path.join(vc_system.states_dir, state_name))


def _resolve_provisional_state(vc_system, todo_text):
    """Reconcile TODO markers with authoritative VC log records.

    Returns (active_records, todo_text, error_reason_or_None).

    - active record + TODO `[ ]`  → admission was interrupted; repair to `[-]`
    - TODO `[-]` without an active record → fail closed
    - missing base or candidate snapshot → fail closed
    """
    active = _provisional_records(vc_system)
    tasks = todo_mod.parse_todo(todo_text)
    numbers = {task["number"] for task in tasks}

    # Ignore records for tasks no longer in the TODO.
    active = {n: rec for n, rec in active.items() if n in numbers}

    changed = False
    for task in tasks:
        number = task["number"]
        if task["state"] == "provisional" and number not in active:
            return None, todo_text, (
                f"inconsistent persisted state: T{number} is provisional "
                "without a valid provisional record"
            )
        if task["state"] == "unchecked" and number in active:
            todo_text = todo_mod.set_task_provisional(todo_text, number)
            changed = True

    for number, rec in active.items():
        if not _snapshot_exists(vc_system, rec["base"]):
            return None, todo_text, (
                f"inconsistent persisted state: T{number} base snapshot "
                f"{rec['base']} missing"
            )
        if not _snapshot_exists(vc_system, rec["candidate"]):
            return None, todo_text, (
                f"inconsistent persisted state: T{number} candidate snapshot "
                f"{rec['candidate']} missing"
            )

    if changed:
        write_file_content(os.path.join(vc_system.workspace, "docs/todo.md"), todo_text)
    return active, todo_text, None



def ensure_workspace(workspace):
    docs = os.path.join(workspace, "docs")
    os.makedirs(docs, exist_ok=True)
    write_file_content(os.path.join(docs, "manager.md"), load_prompt("manager"))
    write_file_content(os.path.join(docs, "worker.md"), load_prompt("worker"))


# ── Worker session ───────────────────────────────────────────────────

MAX_WORKER_RESPAWNS = 3

def run_worker_session(number, config, backend=None, feedback=None, task_base_state=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    base_state = task_base_state or vc_system.get_current()
    obs = get_log(workspace)

    sess_tok = obs.start("worker_session", task=f"T{number}", base_state=base_state, has_feedback=bool(feedback))
    try:
        worker_adapter = adapter_mod.WorkerAdapter(config, number, feedback=feedback)
        b = backend or create_backend(config)
        result = worker_adapter.run(b)
    except Exception as exc:
        result = {"status": "error", "reason": str(exc)}

    if result.get("status") == "done":
        run_evidence = result.get("run_evidence", [])
        description = f"T{number} candidate base={base_state}"
        if run_evidence:
            description += f"\n\n{adapter_mod.format_run_evidence(run_evidence)}"
        try:
            state = vc_system.save_state(
                f"Worker {number}",
                description,
            )
        except Exception:
            obs.end(sess_tok, status="error", reason="vc save failed")
            if base_state:
                vc_system.restore(base_state, preserve_todo=True)
            return {"status": "error", "reason": "vc save failed after worker"}
        obs.event("candidate_submitted", task=f"T{number}", base_state=base_state, candidate_state=state)
        obs.end(sess_tok, status="submitted", candidate_state=state)
        return {
            "status": "submitted",
            "summary": f"T{number} submitted",
            "termination": "normal",
            "state": state,
            "base_state": base_state,
            "run_evidence": run_evidence,
        }

    if result.get("status") in {"stalled", "timeout"}:
        obs.event("rollback", task=f"T{number}", reason=result.get("status"), to_state=base_state)
        vc_system.restore_workspace(base_state, preserve_todo=True)
        vc_system.set_current(base_state)
        obs.end(sess_tok, status=result.get("status"), reason=result.get("reason"))
        return {
            "status": result.get("status"),
            "reason": result.get("reason", f"T{number} {result.get('status', 'error')}"),
            "termination": result.get("status", "error"),
            "base_state": base_state,
        }

    if base_state:
        try:
            vc_system.restore(base_state, preserve_todo=True)
        except Exception as exc:
            obs.end(sess_tok, status="error", reason=f"rollback failed: {exc}")
            return {"status": "error", "reason": f"rollback failed: {exc}"}
    obs.end(sess_tok, status="error", reason=result.get("reason"))
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
            shutil.rmtree(backup_dir, ignore_errors=True)
        os.rename(workspace, backup_dir)

    try:
        if ws_exists:
            shutil.copytree(backup_dir, workspace, symlinks=True)
        else:
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

# ── Manager reconciliation ───────────────────────────────────────────

def _log_section(log_text, state_name):
    """Return the VC log section body under `### {state_name}` (latest)."""
    marker = f"### {state_name}\n"
    start = log_text.rfind(marker)
    if start < 0:
        return ""
    section = log_text[start + len(marker):]
    end = len(section)
    for token in ("\n### ", "\n## "):
        idx = section.find(token)
        if idx >= 0:
            end = min(end, idx)
    return section[:end]


def _run_evidence_from_log(log_text, candidate_state):
    section = _log_section(log_text, candidate_state)
    idx = section.find("RUN evidence:")
    if idx < 0:
        return "(no RUN evidence)"
    return section[idx + len("RUN evidence:"):].strip() or "(no RUN evidence)"


def _collect_reconcile_evidence(vc_system, workspace, tasks, active, todo_text):
    """Build the Manager evidence block for every active provisional task."""
    parts = []
    log_text = vc_system.get_log()
    for number in sorted(active):
        rec = active[number]
        task = todo_mod.get_task(tasks, number)
        description = task["description"] if task else "(unknown)"
        base_root = os.path.join(vc_system.states_dir, rec["base"])
        candidate_root = os.path.join(vc_system.states_dir, rec["candidate"])
        diff = adapter_mod._workspace_diff(base_root, candidate_root)
        run_evidence = _run_evidence_from_log(log_text, rec["candidate"])
        parts.append(
            f"### Task T{number}\n"
            f"Description: {description}\n"
            f"Fixed base: {rec['base']}\n"
            f"Candidate: {rec['candidate']}\n"
            f"Task Reviewer verdict: ACCEPT\n"
            f"RUN evidence:\n{run_evidence}\n"
            f"Base -> candidate diff:\n{diff}"
        )
    return "\n\n".join(parts)


def _simulate_decision(tasks, decision):
    """Hypothetical post-application state. Returns (unchecked, provisional)."""
    states = {task["number"]: task["state"] for task in tasks}
    for number in decision.get("done", []):
        states[number] = "done"
    if decision.get("rework"):
        n0 = decision["rework"]["task"]
        for number in states:
            if number >= n0:
                states[number] = "unchecked"
    new_unchecked = len(decision.get("add", []))
    if decision.get("replace"):
        new_unchecked += len(decision["replace"])
    unchecked = sum(1 for s in states.values() if s == "unchecked") + new_unchecked
    provisional = sum(1 for s in states.values() if s == "provisional")
    return unchecked, provisional


def _validate_reconcile_decision(decision, tasks, active):
    """Full validation of the Manager decision before ANY mutation.

    Returns an error string, or None when the decision is valid.
    """
    numbers = {task["number"] for task in tasks}
    provisional_numbers = set(active)

    done = decision.get("done", [])
    rework = decision.get("rework")
    add = decision.get("add", [])
    replace = decision.get("replace")
    project = decision.get("project")

    if len(done) != len(set(done)):
        return "duplicate task IDs in # Done"
    if any(number not in numbers for number in done):
        return "unknown task ID in # Done"
    if any(number not in provisional_numbers for number in done):
        return "DONE applied to a non-provisional task"

    if rework is not None:
        n0 = rework["task"]
        if n0 not in numbers:
            return "unknown task ID in # Rework"
        if n0 not in provisional_numbers:
            return "REWORK applied to a non-provisional task"
        if not rework.get("reason", "").strip():
            return "REWORK requires a non-empty reason"
        if n0 in done:
            return "task appears in both # Done and # Rework"
        if any(number >= n0 for number in done):
            return "no task at or after the rework task may appear in # Done"
        expected_done = {n for n in provisional_numbers if n < n0}
        if set(done) != expected_done:
            return "every earlier active provisional task must appear in # Done"
    else:
        if set(done) != provisional_numbers:
            return "every active provisional task must appear in # Done"

    if add:
        if len(add) != len(set(add)):
            return "duplicate entries in # Add"
        if any(not item.strip() for item in add):
            return "# Add entries must be non-empty"

    if replace is not None:
        if rework is not None:
            return "# Replace Remaining Plan cannot coexist with # Rework"
        if add:
            return "# Replace Remaining Plan cannot coexist with # Add"
        if not replace:
            return "# Replace Remaining Plan must not be empty"
        if any(not item.strip() for item in replace):
            return "# Replace Remaining Plan entries must be non-empty"
        if not any(task["state"] == "unchecked" for task in tasks):
            return "# Replace Remaining Plan requires currently unchecked tasks"

    if project not in ("CONTINUE", "COMPLETE"):
        return f"invalid # Project value: {project!r}"

    unchecked, provisional = _simulate_decision(tasks, decision)
    if project == "CONTINUE":
        if unchecked == 0:
            return "CONTINUE requires executable unchecked work after application"
        if not (done or rework or add or replace):
            return "CONTINUE causes no state mutation or executable next work"
    if project == "COMPLETE":
        if unchecked > 0 or provisional > 0:
            return "COMPLETE while provisional or unchecked work remains"

    return None


def _apply_reconcile_decision(vc_system, workspace, todo_text, tasks, active, decision, reviewer_feedback):
    """Apply a validated Manager decision. All mutations live here."""
    obs = get_log(workspace)
    done = decision.get("done", [])
    rework = decision.get("rework")
    add = decision.get("add", [])
    replace = decision.get("replace")
    project = decision.get("project")

    if rework is not None:
        n0 = rework["task"]
        base_state = active[n0]["base"]

        # 1. Ratify the declared earlier Done prefix.
        for number in sorted(done):
            todo_text = todo_mod.set_task_checked(todo_text, number, True)
            rec = active[number]
            vc_system.append_log(
                f"ratified: task=T{number} base={rec['base']} candidate={rec['candidate']}"
            )

        # 2-3. Restore the live workspace to the rework task's fixed base.
        vc_system.restore_workspace(base_state, preserve_todo=True)
        vc_system.set_current(base_state)

        # 4. Uncheck Tn and every later provisional task.
        for number in sorted(active):
            if number >= n0:
                todo_text = todo_mod.set_task_checked(todo_text, number, False)

        # 5. Invalidate records for the entire suffix.
        for number in sorted(active):
            if number >= n0:
                rec = active[number]
                vc_system.append_log(
                    f"invalidate: task=T{number} base={rec['base']} "
                    f"candidate={rec['candidate']} reason=rework-suffix"
                )

        # 6. Preserve normalized feedback for Tn only.
        reason = " ".join(rework["reason"].split()).strip()
        reviewer_feedback[n0] = reason
        vc_system._append_log(base_state,
            f"rework_reason: task=T{n0} base={base_state} "
            f"candidate={active[n0]['candidate']} reason={reason}")
        obs.event("rework", task=f"T{n0}", base_state=base_state,
                  candidate=active[n0]["candidate"])
    else:
        for number in sorted(done):
            todo_text = todo_mod.set_task_checked(todo_text, number, True)
            rec = active[number]
            vc_system.append_log(
                f"ratified: task=T{number} base={rec['base']} candidate={rec['candidate']}"
            )
            obs.event("ratify", task=f"T{number}", base=rec["base"],
                      candidate=rec["candidate"])

    if add:
        todo_text = _append_missing_tasks(todo_text, add)
    if replace is not None:
        # Rebuild from the CURRENT todo (already ratified / uncheck mutations),
        # never from the stale pre-decision task list.
        current_tasks = todo_mod.parse_todo(todo_text)
        todo_text = _replace_unchecked_tasks(todo_text, replace, current_tasks)

    write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
    return todo_text, project


def _replace_unchecked_tasks(todo_text, descriptions, tasks):
    """Replace only currently unchecked tasks; keep DONE and provisional lines."""
    kept = []
    for task in tasks:
        if task["state"] == "unchecked":
            continue
        marker = "x" if task["state"] == "done" else "-"
        kept.append(f"- [{marker}] T{task['number']} — {task['description']}")
    max_number = max((task["number"] for task in tasks if task["state"] != "unchecked"),
                     default=0)
    for description in descriptions:
        max_number += 1
        kept.append(f"- [ ] T{max_number} — {description.strip()}")
    return "\n".join(kept) + "\n"


def _reconcile_batch(vc_system, config, backend, active, todo_text, reviewer_feedback):
    """One Manager reconciliation of the current provisional batch."""
    workspace = config["workspace"]
    obs = get_log(workspace)
    tasks = todo_mod.parse_todo(todo_text)
    task_md = read_file_content(os.path.join(workspace, "docs/task.md"))
    evidence = _collect_reconcile_evidence(vc_system, workspace, tasks, active, todo_text)

    adapter = adapter_mod.ManagerReconcileAdapter(config, task_md, todo_text, evidence)
    decision = None
    correction = None
    for attempt in range(adapter_mod.ManagerReconcileAdapter.RETRY_LIMIT):
        decision, error = adapter.run_once(backend, correction=correction)
        if decision is not None:
            validation_error = _validate_reconcile_decision(decision, tasks, active)
            if validation_error is None:
                break
            error = validation_error
        correction = (
            f"No valid reconciliation decision was found. {error} "
            "Return only the reconciliation sections."
        )
        decision = None
    if decision is None:
        return {"status": "error", "reason": correction or "failed to produce valid reconciliation"}

    todo_text, project = _apply_reconcile_decision(
        vc_system, workspace, todo_text, tasks, active, decision, reviewer_feedback
    )
    obs.event("manager_decision", project=project, done=decision.get("done", []),
              rework=decision.get("rework"), add=decision.get("add", []))

    if project == "COMPLETE":
        return {"status": "done"}

    # REWORK already restored the workspace and rewound `current` to the
    # task's fixed base; saving another state would break restart feedback
    # recovery, which reads the latest `### {current}` section.
    if decision.get("rework") is None:
        vc_system.save_state("Manager (reconcile)", "project reconciliation")
    return {"status": "continue"}


def run_project(config, backend=None):
    obs = get_log(config["workspace"])
    run_tok = obs.start("run_project")
    try:
        return _run_project_inner(config, backend)
    finally:
        obs.end(run_tok)


def _run_project_inner(config, backend=None):
    workspace = config["workspace"]
    ensure_workspace(workspace)
    vc_system = vc_mod.VersionControl(workspace)
    backend = backend or create_backend(config)
    reviewer_feedback = {}
    respawn_counts = {}
    current_task_number = None
    current_task_base_state = None
    obs = get_log(workspace)

    while True:
        todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
        tasks = todo_mod.parse_todo(todo_text)

        if not tasks:
            return {"status": "error", "reason": "no TODO tasks"}

        # Resolve authoritative provisional state from the VC log; repair
        # interrupted admissions and fail closed on inconsistency.
        active, todo_text, resolve_err = _resolve_provisional_state(vc_system, todo_text)
        if resolve_err:
            return {"status": "error", "reason": resolve_err}
        tasks = todo_mod.parse_todo(todo_text)

        unchecked = todo_mod.first_unchecked(tasks)

        # Batch boundary: reconcile when the provisional count reaches the
        # batch limit OR no unchecked task remains. An empty batch (all tasks
        # already DONE) still requires the Manager to declare COMPLETE.
        batch = config.get("provisional_batch", 4)
        if len(active) >= batch or unchecked is None:
            result = _reconcile_batch(vc_system, config, backend, active, todo_text,
                                      reviewer_feedback)
            if result["status"] == "error":
                return result
            if result["status"] == "done":
                return result
            current_task_number = None
            current_task_base_state = None
            continue

        number = unchecked["number"]
        if current_task_number != number or current_task_base_state is None:
            current_task_number = number
            current_task_base_state = _task_base_state(vc_system, task_number=number)
        feedback = reviewer_feedback.get(number) or _task_rework_reason(vc_system)
        if feedback:
            reviewer_feedback[number] = feedback
        print(f"Worker {number}...")
        try:
            result = run_worker_session(number, config, backend=backend, feedback=feedback,
                                         task_base_state=current_task_base_state)
        except Exception as exc:
            return {"status": "error", "reason": f"Worker {number} exception: {exc}"}
        if result["status"] in {"stalled", "timeout"}:
            respawn_counts[number] = respawn_counts.get(number, 0) + 1
            obs.event("respawn", task=f"T{number}", reason=result.get("status"), count=respawn_counts[number])
            print(f"Worker {number} {result['status']}: {result.get('reason', 'unknown')}")
            if respawn_counts[number] > MAX_WORKER_RESPAWNS:
                return {
                    "status": "error",
                    "reason": f"Worker {number} exceeded respawn limit",
                    "detail": result,
                }
            continue
        if result["status"] != "submitted":
            print(f"Worker {number} failed: {result.get('reason', 'unknown')}")
            return {"status": "error", "reason": f"Worker {number} failed", "detail": result}
        print(f"Worker {number} submitted T{number} ({result['termination']}, {result['state']}).")
        respawn_counts.pop(number, None)

        review = None
        try:
            review = adapter_mod.TaskReviewAdapter(
                config,
                number,
                base_state=current_task_base_state,
                candidate_state=result.get("state"),
            ).run(backend)
        except Exception as exc:
            obs.event("review_exception", task=f"T{number}", error=str(exc))
            return {"status": "error", "reason": f"review exception: {exc}"}

        obs.event("reviewer_verdict", task=f"T{number}", verdict=review.get("verdict"),
                  base_state=current_task_base_state, candidate_state=result.get("state"))
        if review.get("verdict") == "ERROR":
            return {"status": "error", "reason": review.get("reason", "review error"), "detail": review}

        if review.get("verdict") == "REWORK":
            reviewer_feedback[number] = " ".join(review.get("reason", "").split()).strip()
            print(f"Worker {number} rework: {reviewer_feedback[number]}")
            obs.event("rework", task=f"T{number}", base_state=current_task_base_state,
                      candidate_state=result.get("state"))
            if current_task_base_state:
                vc_system.restore_workspace(current_task_base_state, preserve_todo=True)
                vc_system.set_current(current_task_base_state)
                obs.event("rollback", task=f"T{number}", to_state=current_task_base_state)
            vc_system._append_log(current_task_base_state or result["state"],
                f"rework_reason: task=T{number} base={current_task_base_state} "
                f"candidate={result['state']} reason={reviewer_feedback[number]}")
            continue

        if review.get("verdict") == "ACCEPT":
            reviewer_feedback.pop(number, None)
            obs.event("accept", task=f"T{number}", candidate_state=result.get("state"))
            # Record-first admission: the provisional record is appended to the
            # append-only VC log BEFORE the TODO marker is flipped to `[-]`.
            vc_system.append_log(
                f"provisional: task=T{number} base={current_task_base_state} "
                f"candidate={result['state']} review=ACCEPT"
            )
            todo_text = read_file_content(os.path.join(workspace, "docs/todo.md"))
            todo_text = todo_mod.set_task_provisional(todo_text, number)
            write_file_content(os.path.join(workspace, "docs/todo.md"), todo_text)
            current_task_number = None
            current_task_base_state = None
            continue

        return {"status": "error", "reason": f"unexpected review verdict: {review.get('verdict', '?')}"}


def show_status(config):
    workspace = config["workspace"]
    if not os.path.exists(os.path.join(workspace, ".bid")):
        print("No BID project in workspace.")
        return
    current = vc_mod.VersionControl(workspace).get_current() or "?"
    tasks = todo_mod.parse_todo(read_file_content(os.path.join(workspace, "docs/todo.md")))
    checked = sum(1 for task in tasks if task["state"] == "done")
    provisional = sum(1 for task in tasks if task["state"] == "provisional")
    print(f"VC state: {current}")
    print(f"Tasks:    {checked}/{len(tasks)} done, {provisional} provisional")
    print(f"Done:     {'yes' if tasks and todo_mod.all_checked(tasks) else 'no'}")
    markers = {"done": "x", "provisional": "-", "unchecked": " "}
    for task in tasks:
        print(f"  [{markers[task['state']]}] {task['id']} — {task['description']}")
