import difflib
import hashlib
import inspect
import os
import shlex
import re
import subprocess
import sys
import shutil
import stat
import tempfile
import time

from . import permissions
from . import protocol
from . import search as search_mod
from . import todo as todo_mod
from . import vc as vc_mod
from .observability import get_log
from .observer import Observer


# ── Helpers ──────────────────────────────────────────────────────────

def _read(workspace, rel):
    path = _safe_path(workspace, rel)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(workspace, rel, content):
    path = _safe_path(workspace, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _safe_path(workspace, rel):
    safe, err, norm = permissions.check_path_safety(rel, workspace)
    if not safe:
        raise ValueError(f"path safety violation: {err}")
    return os.path.join(workspace, norm)


def _clean_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    return text


def _hash_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return "?"


def _file_metadata(path):
    with open(path, "rb") as file:
        data = file.read()
    trailing = data[len(data.rstrip(b"\r\n")):]
    count = len(re.findall(rb"\r\n|\r|\n", trailing))
    ending = (
        "CRLF" if data.endswith(b"\r\n") else
        "LF" if data.endswith(b"\n") else
        "CR" if data.endswith(b"\r") else "none"
    )
    return f"bytes={len(data)};end={ending};trailing={count}"


RUN_OUTPUT_LIMIT = 2000

CONTROL_ROOTS = (
    ".bid",
    "docs/task.md",
    "docs/todo.md",
    "docs/worker.md",
    "docs/manager.md",
    "docs/project-status.md",
    "docs/decisions.md",
    "docs/reviews",
)

TASK_REVIEWER_SYSTEM = """You are BID Task Reviewer.
Judge whether the candidate satisfies the assigned task using only the
provided original request, task, fixed-base-to-candidate diff, and candidate
workspace snapshot. Check every concrete deliverable against that evidence.
The snapshot marks each file's UTF-8 byte count and exact trailing newline count.
Missing files, incorrect literal content, or an empty diff when the task requires
a change are concrete unmet requirements. When the task specifies a trailing
newline count, compare it exactly with the snapshot's `trailing=` value. A
Worker `Done` command is not proof of completion. ACCEPT only when the evidence
supports the current task; otherwise REWORK with the specific requirement.
The verdict must agree with the reason: if the reason identifies a missing,
incorrect, or unmet requirement, return REWORK, never ACCEPT.

Return exactly one of:

ACCEPT
Reason: <reason>

REWORK
Reason: <reason>

Return only the verdict and reason. Do not return a checklist, analysis,
Markdown fences, or additional text."""

def _bounded_text(text, limit=RUN_OUTPUT_LIMIT):
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = (text or "").rstrip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _indented_text(text):
    text = _bounded_text(text)
    if not text:
        text = "(empty)"
    return "\n".join((f"  {line}" if line else "") for line in text.splitlines())


def _log_worker_event(vc_system, label, text):
    vc_system.append_log(f"\n{label}:\n{_indented_text(text)}\n")


def _command_label(cmd):
    if cmd["type"] == "READ":
        return f"READ {cmd['path']}"
    if cmd["type"] == "WRITE":
        return f"WRITE {cmd['path']}"
    if cmd["type"] == "WRITE_UNTERMINATED":
        return f"WRITE {cmd['path']} [unterminated]"
    if cmd["type"] == "WRITE_FENCE_POLLUTED":
        return f"WRITE {cmd['path']} [fence]"
    if cmd["type"] == "REPLACE":
        return f"REPLACE {cmd['path']}"
    if cmd["type"] == "REPLACE_UNTERMINATED":
        return f"REPLACE {cmd['path']} [unterminated]"
    if cmd["type"] == "RUN":
        return f"RUN {cmd['command']}"
    if cmd["type"] == "Done":
        return "Done"
    return cmd["type"]


def _snapshot_control_state(workspace):
    snapshot = tempfile.mkdtemp(prefix="bid-control-")
    for rel in CONTROL_ROOTS:
        source = os.path.join(workspace, rel)
        if not os.path.lexists(source):
            continue
        target = os.path.join(snapshot, rel)
        if not os.path.islink(source) and os.path.isdir(source):
            shutil.copytree(source, target, symlinks=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
    return snapshot


def _entry_signature(path):
    if os.path.islink(path):
        return ("symlink", os.readlink(path))
    if not os.path.exists(path):
        return None
    if os.path.isfile(path):
        with open(path, "rb") as file:
            return ("file", hashlib.sha256(file.read()).hexdigest())
    if os.path.isdir(path):
        return (
            "dir",
            tuple(
                sorted(
                    (name, _entry_signature(os.path.join(path, name)))
                    for name in os.listdir(path)
                )
            ),
        )
    return ("other", None)


def _control_state_changed(workspace, snapshot):
    for rel in CONTROL_ROOTS:
        if _entry_signature(os.path.join(workspace, rel)) != _entry_signature(os.path.join(snapshot, rel)):
            return True
    return False


def _restore_control_state(workspace, snapshot):
    for rel in sorted(CONTROL_ROOTS, key=lambda item: item.count("/"), reverse=True):
        target = os.path.join(workspace, rel)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        elif os.path.lexists(target):
            os.remove(target)

    for rel in CONTROL_ROOTS:
        source = os.path.join(snapshot, rel)
        if not os.path.lexists(source):
            continue
        target = os.path.join(workspace, rel)
        if not os.path.islink(source) and os.path.isdir(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copytree(source, target, symlinks=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)


def format_run_evidence(entries):
    if not entries:
        return "(no RUN evidence)"

    lines = ["RUN evidence:"]
    for index, entry in enumerate(entries, start=1):
        if index > 1:
            lines.append("")
        lines.append(f"RUN #{index}")
        lines.append(f"command: {entry['command']}")
        lines.append(f"result: {entry['result']}")
        lines.append(f"timed_out: {'yes' if entry.get('timed_out') else 'no'}")
        lines.append(f"exit_code: {entry['exit_code']}")
        lines.append("stdout:")
        lines.append(_bounded_text(entry.get('stdout')) or "(empty)")
        lines.append("stderr:")
        lines.append(_bounded_text(entry.get('stderr')) or "(empty)")
    return "\n".join(lines)


def _validate_direct_deletion(argv, workspace):
    if os.path.basename(argv[0]) not in {"rm", "rmdir", "unlink"}:
        return None

    operands = []
    options_done = False
    for arg in argv[1:]:
        if arg == "--":
            options_done = True
            continue
        if not options_done and arg.startswith("-"):
            continue
        operands.append(arg)

    for path in operands:
        safe, error, rel = permissions.check_path_safety(path, workspace)
        if not safe:
            return f"error: deletion denied: {error}"
        if rel == ".":
            return "error: deletion denied: workspace root"
        for protected in CONTROL_ROOTS:
            if rel == protected or rel.startswith(protected + "/") or protected.startswith(rel + "/"):
                return f"error: deletion denied: protected path {rel}"

    return None


# ── Command parsing ──────────────────────────────────────────────────

_KNOWN_CMDS = {"READ ", "WRITE ", "RUN ", "SEARCH ", "REPLACE "}


def available_command_names(config):
    names = ["READ"]
    if config.get("max_searches_per_worker", 10) > 0:
        names.append("SEARCH")
    names.extend(("WRITE", "REPLACE"))
    if config.get("allow_host_run", False) or callable(config.get("run_executor")):
        names.append("RUN")
    names.append("Done")
    return tuple(names)


def render_worker_policy(policy, config):
    optional = []
    if config.get("max_searches_per_worker", 10) > 0:
        optional.append("SEARCH <query>")
    if config.get("allow_host_run", False) or callable(config.get("run_executor")):
        optional.append("RUN <program> [arguments...]")
    replacement = "\n".join(optional) if optional else "No optional commands are enabled for this session."
    return policy.replace("{{OPTIONAL_COMMANDS}}", replacement)


def _run_backend(backend, messages, tools, max_tokens, timeout=None, grammar=None):
    parameters = inspect.signature(backend.run).parameters.values()
    accepts_timeout = any(p.name == "timeout" or p.kind == p.VAR_KEYWORD for p in parameters)
    accepts_grammar = any(p.name == "grammar" or p.kind == p.VAR_KEYWORD for p in parameters)
    kwargs = {"max_tokens": max_tokens}
    if timeout is not None and accepts_timeout:
        kwargs["timeout"] = timeout
    if grammar is not None:
        if not accepts_grammar:
            raise TypeError("protocol grammar requires a backend that accepts grammar")
        kwargs["grammar"] = grammar
    return backend.run(messages, tools, **kwargs)


def _parse_content_into_turns(content, finish_reason=None):
    lines = content.split("\n")
    commands = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "Done":
            commands.append({"type": "Done"})
            i += 1
            continue

        if stripped.startswith("SEARCH "):
            query = stripped[7:].strip()
            commands.append({"type": "SEARCH", "query": query})
            i += 1
            continue

        if stripped.startswith("READ "):
            path = stripped[5:].strip()
            commands.append({"type": "READ", "path": path})
            i += 1
            continue

        if stripped.startswith("WRITE "):
            path = stripped[6:].strip()
            i += 1
            body_lines = []
            terminated = False
            while i < len(lines):
                if lines[i].strip() == "END WRITE":
                    terminated = True
                    i += 1
                    break
                body_lines.append(lines[i])
                i += 1
            # Reject bodies whose first non-empty line is a Markdown code fence on non-`.md` targets
            first_body = ""
            for line in body_lines:
                if line.strip():
                    first_body = line.strip()
                    break
            if first_body.startswith("```") and not path.endswith(".md") and any(l.strip() for l in body_lines):
                commands.append({"type": "WRITE_FENCE_POLLUTED", "path": path})
                continue
            if not terminated:
                if finish_reason == "stop" and body_lines and any(l.strip() for l in body_lines):
                    has_trailing_cmd = any(
                        l.strip().startswith(cmd)
                        for l in body_lines
                        for cmd in ("READ ", "WRITE ", "RUN ", "Done")
                    )
                    if not has_trailing_cmd:
                        commands.append({
                            "type": "WRITE", "path": path,
                            "content": "\n".join(body_lines),
                            "implicit": True,
                        })
                        continue
                commands.append({"type": "WRITE_UNTERMINATED", "path": path})
                continue
            commands.append({"type": "WRITE", "path": path, "content": "\n".join(body_lines)})
            continue

        if stripped.startswith("RUN "):
            command = stripped[4:].strip()
            commands.append({"type": "RUN", "command": command})
            i += 1
            continue

        if stripped.startswith("REPLACE "):
            path = stripped[8:].strip()
            i += 1
            old_lines = []
            old_complete = False
            while i < len(lines):
                if lines[i].strip() == "---REPLACE_WITH---":
                    old_complete = True
                    i += 1
                    break
                old_lines.append(lines[i])
                i += 1
            if not old_complete:
                commands.append({"type": "REPLACE_UNTERMINATED", "path": path})
                continue
            new_lines = []
            new_complete = False
            while i < len(lines):
                if lines[i].strip() == "END REPLACE":
                    new_complete = True
                    i += 1
                    break
                new_lines.append(lines[i])
                i += 1
            if not new_complete:
                commands.append({"type": "REPLACE_UNTERMINATED", "path": path})
                continue
            commands.append({
                "type": "REPLACE", "path": path,
                "old_text": "\n".join(old_lines),
                "new_text": "\n".join(new_lines),
            })
            continue

        i += 1

    return commands


def _find_unknown_commands(content, commands):
    lines = content.split("\n")
    consumed = set()
    li = 0
    while li < len(lines):
        s = lines[li].strip()
        if s == "Done" or s.startswith("SEARCH ") or s.startswith("READ ") or s.startswith("RUN "):
            consumed.add(li); li += 1; continue
        if s.startswith("WRITE "):
            consumed.add(li); li += 1
            while li < len(lines) and lines[li].strip() != "END WRITE":
                consumed.add(li); li += 1
            if li < len(lines) and lines[li].strip() == "END WRITE":
                consumed.add(li); li += 1
            continue
        if s.startswith("REPLACE "):
            consumed.add(li); li += 1
            while li < len(lines) and lines[li].strip() != "---REPLACE_WITH---":
                consumed.add(li); li += 1
            if li < len(lines) and lines[li].strip() == "---REPLACE_WITH---":
                consumed.add(li); li += 1
            while li < len(lines) and lines[li].strip() != "END REPLACE":
                consumed.add(li); li += 1
            if li < len(lines) and lines[li].strip() == "END REPLACE":
                consumed.add(li); li += 1
            continue
        li += 1
    unknown = []
    for i, line in enumerate(lines):
        if i in consumed:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        tokens = stripped.split()
        # "Done" with trailing text is malformed — produce UNKNOWN.
        # This check must come before the token-count restriction.
        if tokens[0] == "Done" and len(tokens) > 1:
            unknown.append(stripped)
            continue
        if tokens[0] == "Done" and len(tokens) == 1:
            continue  # standalone Done — consumed above
        if len(tokens) > 2:
            continue
        if any(stripped.startswith(p) for p in _KNOWN_CMDS):
            continue
        import re
        if re.match(r"^[A-Z]{2,}$", tokens[0]):
            unknown.append(stripped)
    return unknown
# ── TODO validation ──────────────────────────────────────────────────


def validate_todo_tasks(tasks):
    if not tasks:
        return False, "no tasks"
    numbers = [t["number"] for t in tasks]
    if numbers != list(range(1, len(tasks) + 1)):
        return False, f"task numbers must be sequential T1..T{len(tasks)}, got {numbers}"
    for t in tasks:
        if t["id"] != f"T{t['number']}":
            return False, f"{t['id']} has noncanonical format (expected T{t['number']})"
        if t["checked"]:
            return False, f"{t['id']} must start unchecked"
        if not t["description"].strip():
            return False, f"{t['id']} has empty description"
        if t["description"].strip().casefold() in {"description", "task", "todo item"}:
            return False, f"{t['id']} has a placeholder description"
    return True, None


# ── Adapters ─────────────────────────────────────────────────────────

class ManagerInitAdapter:
    """BID reads manager.md + task.md, model returns plain Markdown TODO, BID writes."""

    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]
        self.obs = get_log(self.workspace)

    def run(self, backend):
        if hasattr(backend, "role"):
            backend.role = "manager"
        manager_md = _read(self.workspace, "docs/manager.md")
        task_md = _read(self.workspace, "docs/task.md")

        messages = [
            {"role": "system", "content": manager_md},
            {
                "role": "user",
                "content": (
                    f"{task_md.rstrip()}\n\n"
                    "Create a short, ordered checklist that preserves every deliverable and explicit constraint. "
                    "Each distinct deliverable must appear exactly once. If all requirements concern one "
                    "standalone file, use exactly one task for that file; do not split writing it into a "
                    "second create, inspect, or verification task. Each item must name its target file or "
                    "files and include the public interface and relevant "
                    "input/output requirements the Worker needs. For literal content, copy all required text "
                    "verbatim into the item; do not split content lines into tasks. Every item must change a file. Do not add "
                    "no-op or verification-only items. Return only unchecked Markdown checklist lines:\n\n"
                    "Use the exact prefix `- [ ] ` for each line, followed by a concrete file-producing action. "
                    "Do not use placeholder descriptions.\n"
                ),
            },
        ]

        for attempt in range(self.RETRY_LIMIT):
            req_tok = self.obs.start("model_request", role="manager", retry=attempt)
            try:
                response = _run_backend(
                    backend, messages, [], self.config.get("max_tokens", 32768),
                    grammar=protocol.grammar_for(self.config, "manager"),
                )
            except Exception as exc:
                self.obs.end(req_tok, error=str(exc))
                return {"status": "error", "reason": f"model request failed: {exc}"}
            usage = response.get("usage") or {}
            self.obs.end(
                req_tok,
                finish_reason=response.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )

            content = response.get("content") or ""
            todo = self._todo(content)

            if todo:
                _write(self.workspace, "docs/todo.md", todo)
                return {"status": "success", "todo": todo}

            if attempt < self.RETRY_LIMIT - 1:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "Return only checklist lines in this exact format:\n"
                        "Use the exact prefix `- [ ] ` followed by a concrete file-producing action. "
                        "Do not use placeholder descriptions.\n"
                        "No commentary, headings, or code fences. Each item must change a file. Got:\n\n"
                        + content.strip()[:500]
                    ),
                })

        return {"status": "error", "reason": "failed to produce valid TODO after 3 attempts"}

    @staticmethod
    def _todo(text):
        descriptions = []
        for line in text.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*[-*]\s+\[ \]\s+(.+?)\s*", line)
            if not match:
                return None
            description = match.group(1).strip()
            label = re.fullmatch(r"T\d+\b\s*(?:[—–-]\s*)?(.*)", description)
            if label:
                description = label.group(1).strip()
            if not description:
                return None
            descriptions.append(description)
        if not descriptions:
            return None
        return "\n".join(f"- [ ] T{index} — {description}" for index, description in enumerate(descriptions, 1))


class WorkerAdapter:
    MAX_SOFT_RESETS = 3

    def __init__(self, config, task_number, search_provider=None, feedback=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self._vc = vc_mod.VersionControl(self.workspace)
        self._search_provider = search_provider or search_mod.create_provider(config)
        self._search_cache = search_mod.SearchCache(self.workspace)
        self._search_count = 0
        self._search_limit = config.get("max_searches_per_worker", 10)
        self._cache_hits = 0
        self.feedback = feedback or ""
        self._run_evidence = []
        self._implicit_write_count = 0
        self.obs = get_log(self.workspace)

    def run(self, backend):
        if hasattr(backend, "role"):
            backend.role = "worker"
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"status": "error", "reason": f"T{self.task_number} not found in TODO"}

        worker_policy = render_worker_policy(
            _read(self.workspace, "docs/worker.md"), self.config
        )
        task_prompt = (
            f"\nTask T{self.task_number}: {task['description']}\n\n"
        )
        task_statement = (self.config.get("task_statement") or "").strip()
        if task_statement:
            task_prompt += f"Full public issue statement:\n{task_statement}\n\n"
        if self.feedback:
            task_prompt += f"\nPrevious reviewer feedback:\n{self.feedback}\n"

        messages = [
            {"role": "system", "content": worker_policy},
            {
                "role": "user",
                "content": (
                    f"{task_prompt}"
                ),
            },
        ]

        observer = Observer(self.workspace, self.task_number)
        worker_timeout = self.config.get("worker_timeout", 3600)
        inactivity_timeout = self.config.get("inactivity_timeout", 600)
        repeat_limit = self.config.get("repeat_action_limit", 5)
        session_start = time.monotonic()
        soft_resets = 0
        last_sig = None
        turn_repeat = 0
        last_turn_signature = None
        repeated_turns = 0
        _read_tracker = {}  # canonical_rel_path → (useful_count, last_hash)

        def workspace_state_signature():
            state = repr(sorted(
                (path, digest)
                for path, digest in observer._snapshot.items()
                if not _review_path_blocked(path)
                and not path.startswith((".pytest_cache/", "__pycache__/"))
            ))
            return hashlib.sha256(state.encode("utf-8")).digest()

        previous_state = workspace_state_signature()
        seen_states = {previous_state}
        repeated_state_revisits = 0

        while True:
            remaining = worker_timeout - (time.monotonic() - session_start)
            if remaining <= 0:
                return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}
            req_tok = self.obs.start("model_request", role="worker", task=f"T{self.task_number}", attempt=1)
            try:
                response = _run_backend(
                    backend, messages, [], self.config.get("max_tokens", 32768),
                    min(remaining, self.config.get("request_timeout", remaining)),
                    grammar=protocol.grammar_for(self.config, "worker"),
                )
            except Exception as exc:
                self.obs.end(req_tok, error=str(exc))
                return {"status": "error", "reason": f"model request failed: {exc}"}
            usage = response.get("usage") or {}
            self.obs.end(
                req_tok,
                finish_reason=response.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )

            raw_content = response.get("content") or ""
            if time.monotonic() - session_start >= worker_timeout:
                return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}
            content = raw_content.strip()
            if os.environ.get("BID_IMPLICIT_WRITE") == "1":
                finish_reason = response.get("finish_reason", "stop")
            else:
                finish_reason = None

            messages.append({"role": "assistant", "content": content or "[no output]"})
            _log_worker_event(self._vc, "worker raw response", raw_content)
            changed = False
            useful = False
            saw_done = False
            fence_violation = False
            replace_failed = False
            policy_violation = False

            if content:
                commands = _parse_content_into_turns(content, finish_reason)
            else:
                commands = []

            if not commands:
                _log_worker_event(self._vc, "worker parsed command", "(none)")
                sig = "no_commands"
                if sig == last_sig:
                    turn_repeat += 1
                else:
                    turn_repeat = 0
                last_sig = sig
                messages.append({
                    "role": "user",
                    "content": f"No executable BID command was found. Respond only with actual {', '.join(available_command_names(self.config))} commands. Do not explain or describe the commands."
                })
            else:
                for cmd in commands:
                    remaining = worker_timeout - (time.monotonic() - session_start)
                    if remaining <= 0:
                        return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}
                    _log_worker_event(self._vc, "worker parsed command", _command_label(cmd))
                    cmd_tok = self.obs.start("command", task=f"T{self.task_number}", type=cmd["type"])
                    if cmd["type"] == "Done":
                        saw_done = True
                        _log_worker_event(self._vc, "worker result", "done")
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "READ":
                        try:
                            safe, err, rel = permissions.check_path_safety(cmd["path"], self.workspace)
                            if not safe:
                                result = err
                            else:
                                allowed, err_msg = permissions.check_read_permission(
                                    rel, permissions.ROLE_WORKER, self.task_number, self.workspace
                                )
                                if not allowed:
                                    result = f"permission denied: {err_msg}"
                                else:
                                    abs_path = os.path.join(self.workspace, rel)
                                    if not os.path.exists(abs_path):
                                        result = f"file not found: {cmd['path']}"
                                    elif not os.path.isfile(abs_path):
                                        result = f"not a file: {cmd['path']}"
                                    else:
                                        result = (
                                            f"File metadata: {_file_metadata(abs_path)}\n"
                                            f"File contents:\n{_read(self.workspace, rel)}"
                                        )
                                        fhash = _hash_file(abs_path)
                                        prev = _read_tracker.get(rel)
                                        if not prev or prev[1] != fhash:
                                            useful = True
                                        _read_tracker[rel] = (1 if not prev else prev[0] + 1, fhash)
                        except ValueError as e:
                            result = str(e)
                        sig = f"READ {rel}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "SEARCH":
                        if self._search_limit <= 0:
                            result = "error: SEARCH disabled for this session"
                        elif self._search_count >= self._search_limit:
                            result = f"error: search limit ({self._search_limit}) reached"
                        else:
                            self._search_count += 1  # all attempts count toward ceiling
                            path, n, err, is_cache = search_mod.execute_search(
                                self.workspace, self.task_number, cmd["query"],
                                self._search_cache, self._search_provider,
                            )
                            if err:
                                result = f"error: search failed: {err}. Try a different query."
                            else:
                                if is_cache:
                                    self._cache_hits += 1
                                    result = f"Search cache hit. Evidence at {path}."
                                else:
                                    result = f"Search completed. {n} source(s) saved to {path}."
                                    useful = True
                                if observer.poll_changes():
                                    changed = True
                        sig = f"SEARCH {search_mod._query_hash(cmd['query'])}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "WRITE":
                        try:
                            result = self._write_command(cmd["path"], cmd["content"])
                            if not result.startswith("error"):
                                if cmd.get("implicit"):
                                    self._implicit_write_count += 1
                                if observer.poll_changes():
                                    changed = True
                        except ValueError as e:
                            result = str(e)
                        sig = f"WRITE {cmd['path']}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "RUN":
                        control_snapshot = _snapshot_control_state(self.workspace)
                        control_changed = False
                        ordinary_changed = False
                        evidence_count_before = len(self._run_evidence)
                        try:
                            try:
                                remaining = worker_timeout - (time.monotonic() - session_start)
                                if remaining <= 0:
                                    self.obs.end(cmd_tok, status="timeout")
                                    return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}
                                result = self._run_command(cmd["command"], remaining=remaining)
                            except Exception as exc:
                                result = f"error: execution failed: {exc}"

                            control_changed = _control_state_changed(self.workspace, control_snapshot)
                            if control_changed:
                                _restore_control_state(self.workspace, control_snapshot)
                            ordinary_changed = bool(observer.poll_changes())
                        finally:
                            shutil.rmtree(control_snapshot, ignore_errors=True)

                        denied_deletion = (
                            len(self._run_evidence) > evidence_count_before
                            and self._run_evidence[-1]["denied_deletion"]
                        )
                        if control_changed or denied_deletion:
                            _log_worker_event(self._vc, "worker result", result)
                            sig = f"RUN {cmd['command']}|policy violation"
                            if sig == last_sig:
                                turn_repeat += 1
                            else:
                                turn_repeat = 0
                            last_sig = sig
                            if control_changed:
                                _log_worker_event(self._vc, "worker recovery", "restored protected control state")
                                violation = "policy violation: protected control state changed; restored"
                            else:
                                violation = "policy violation: protected deletion denied"
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"{violation}\n"
                                    f"command result:\n{_indented_text(result)}"
                                ),
                            })
                            policy_violation = True
                            observer.mark_activity()
                            self.obs.end(cmd_tok, policy_violation=True)
                            break

                        _log_worker_event(self._vc, "worker result", result)
                        sig = f"RUN {cmd['command']}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        if ordinary_changed:
                            changed = True
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "WRITE_FENCE_POLLUTED":
                        result = f"error: WRITE {cmd['path']} body starts with a Markdown code fence. Send raw file content without ``` fences, code blocks, or formatting."
                        fence_violation = True
                        sig = f"WRITE_FENCE_POLLUTED"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "REPLACE":
                        result = self._replace_text(cmd["path"], cmd["old_text"], cmd["new_text"])
                        if result.startswith("error"):
                            replace_failed = True
                        if not result.startswith("error"):
                            if observer.poll_changes():
                                changed = True
                        sig = f"REPLACE {cmd['path']}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "REPLACE_UNTERMINATED":
                        result = f"error: REPLACE {cmd['path']} missing ---REPLACE_WITH--- or END REPLACE delimiter"
                        replace_failed = True
                        sig = f"REPLACE_UNTERMINATED"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "WRITE_UNTERMINATED":
                        result = f"error: WRITE {cmd['path']} must end with END WRITE on its own line"
                        sig = f"WRITE_UNTERMINATED"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    result = f"error: unknown command {cmd['type']}"
                    _log_worker_event(self._vc, "worker result", result)
                    messages.append({"role": "user", "content": result})
                    self.obs.end(cmd_tok)

            if commands:
                turn_signature = tuple(tuple(sorted(cmd.items())) for cmd in commands)
                if turn_signature == last_turn_signature and not changed and not useful:
                    repeated_turns += 1
                else:
                    repeated_turns = 0
                last_turn_signature = turn_signature

            # Unknown-command feedback
            unknown_cmds = _find_unknown_commands(raw_content, commands)
            if unknown_cmds:
                err_msg = ("error: unknown command(s): " + ", ".join(unknown_cmds[:3])
                           + f"\nAllowed commands are {', '.join(available_command_names(self.config))}.")
                messages.append({"role": "user", "content": err_msg})
                _log_worker_event(self._vc, "worker result", err_msg)
                # NOT counted as useful or changed; does NOT reset repeat

            # Done processing
            if policy_violation or fence_violation or replace_failed:
                saw_done = False

            if time.monotonic() - session_start >= worker_timeout:
                return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}
            if saw_done:
                return {"status": "done", "checked": False, "run_evidence": list(self._run_evidence)}

            current_state = workspace_state_signature()
            if useful:
                seen_states = {current_state}
                repeated_state_revisits = 0
            elif current_state != previous_state:
                if current_state in seen_states:
                    repeated_state_revisits += 1
                    if repeated_state_revisits >= repeat_limit:
                        return {
                            "status": "stalled",
                            "reason": "workspace repeatedly returned to earlier states",
                        }
                else:
                    seen_states.add(current_state)
                    repeated_state_revisits = 0
            previous_state = current_state

            # Soft reset on repeat stall
            if (turn_repeat >= repeat_limit or repeated_turns >= repeat_limit) and not changed and not useful:
                soft_resets += 1
                if soft_resets > self.MAX_SOFT_RESETS:
                    return {"status": "stalled", "reason": f"repeated action without progress"}
                system = messages[0]
                messages = [
                    system,
                    {
                        "role": "user",
                        "content": (
                            f"{task_prompt}\n\n"
                            f"[ERROR: did not make progress. "
                            f"Last command: {last_sig.split('|')[0] if '|' in last_sig else last_sig}. "
                            f"Try a different approach.]"
                        ),
                    },
                ]
                observer = Observer(self.workspace, self.task_number)
                last_sig = None
                turn_repeat = 0
                last_turn_signature = None
                repeated_turns = 0
                continue

            # Activity marking
            if useful or changed:
                observer.mark_activity()

            if observer.inactive_for() > inactivity_timeout:
                return {"status": "timeout", "reason": f"inactive {observer.inactive_for():.0f}s"}

        return {"status": "timeout", "reason": f"Worker deadline {worker_timeout}s exceeded"}

    def _write_command(self, path, content):
        if re.search(r"[<>|;&`'\"]", path):
            raise ValueError(f"malformed WRITE path: {path}")

        safe, err, rel = permissions.check_path_safety(path, self.workspace)
        if not safe:
            raise ValueError(err)

        allowed, err_msg = permissions.check_write_permission(
            rel, permissions.ROLE_WORKER, self.task_number, self.workspace
        )
        if not allowed:
            raise ValueError(f"permission denied: {err_msg}")

        _write(self.workspace, rel, content)
        abs_path = os.path.join(self.workspace, rel)
        metadata = _file_metadata(abs_path)
        return f"wrote {os.path.getsize(abs_path)} bytes to {rel}; {metadata.split(';', 1)[1]}"

    def _replace_text(self, path, old_text, new_text):
        if not old_text:
            return "error: old_text required"
        safe, err, rel = permissions.check_path_safety(path, self.workspace)
        if not safe:
            return err
        allowed, err_msg = permissions.check_write_permission(
            rel, permissions.ROLE_WORKER, self.task_number, self.workspace
        )
        if not allowed:
            return f"permission denied: {err_msg}"
        abs_path = os.path.join(self.workspace, rel)
        if not os.path.exists(abs_path):
            return f"file not found: {path}"
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                current = f.read()
        except Exception as exc:
            return f"error reading {rel}: {exc}"
        count = current.count(old_text)
        if count == 0:
            return f"error: old_text not found in {rel}. READ the file again and copy a smaller exact block (verbatim, preserving indentation)."
        if count > 1:
            return f"error: old_text matches {count} occurrences in {rel}; must match exactly one"
        updated = current.replace(old_text, new_text, 1)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(updated)
            if self._obs_check_changes(abs_path, current):
                pass
            return f"replaced text in {rel}"
        except Exception as exc:
            return f"error writing {rel}: {exc}"

    def _obs_check_changes(self, path, before):
        pass  # hook for observer; real check via poll_changes in dispatch

    def _run_command(self, command, remaining=None):
        t0 = time.monotonic()
        try:
            return self._run_command_inner(command, remaining=remaining)
        finally:
            evidence = self._run_evidence[-1] if self._run_evidence else None
            if evidence is not None and evidence.get("command") == (command or "").strip():
                self.obs.event(
                    "run_command",
                    command=(command or "").strip()[:200],
                    duration_s=round(time.monotonic() - t0, 3),
                    exit_code=evidence.get("exit_code"),
                    timed_out=evidence.get("timed_out"),
                    result=evidence.get("result"),
                )

    def _run_command_inner(self, command, remaining=None):
        executor = self.config.get("run_executor")
        if not self.config.get("allow_host_run", False) and not callable(executor):
            return self._record_run_evidence(
                command, "error: RUN disabled; set BID_ALLOW_HOST_RUN=1 only for trusted work",
                False, 126, "", "",
            )
        command = (command or "").strip()
        if not command:
            return self._record_run_evidence(command, "error: command required", False, 127, "", "")

        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return self._record_run_evidence(command, f"error: malformed argv: {exc}", False, 127, "", str(exc))

        if not argv:
            return self._record_run_evidence(command, "error: command required", False, 127, "", "")

        if argv[0] in {"python", "python3"}:
            argv[0] = sys.executable

        error = _validate_direct_deletion(argv, self.workspace)
        if error:
            return self._record_run_evidence(command, error, False, 126, "", "", denied_deletion=True)

        timeout = min(
            self.config.get("run_timeout", 60),
            remaining if remaining is not None else float("inf"),
        )
        if timeout <= 0:
            return self._record_run_evidence(command, "error: Worker session deadline exceeded", True, -1, "", "")
        try:
            if callable(executor):
                completed = executor(argv, cwd=self.workspace, timeout=timeout)
            else:
                completed = subprocess.run(
                    argv,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            result = "success" if completed.returncode == 0 else f"error: exit code {completed.returncode}"
            return self._record_run_evidence(command, result, False, completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError as exc:
            return self._record_run_evidence(command, f"error: command not found: {argv[0]}", False, 127, "", str(exc))
        except PermissionError as exc:
            return self._record_run_evidence(command, f"error: permission denied: {argv[0]}", False, 126, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return self._record_run_evidence(command, f"error: timeout after {timeout}s", True, -1, exc.stdout, exc.stderr)
        except OSError as exc:
            return self._record_run_evidence(command, f"error: execution failed: {exc}", False, 126, "", str(exc))

    def _record_run_evidence(self, command, result, timed_out, exit_code, stdout, stderr, denied_deletion=False):
        evidence = {
            "command": command,
            "result": result,
            "timed_out": timed_out,
            "exit_code": exit_code,
            "stdout": _bounded_text(stdout),
            "stderr": _bounded_text(stderr),
            "denied_deletion": denied_deletion,
        }
        self._run_evidence.append(evidence)
        return (
            f"command: {command}\n"
            f"result: {result}\n"
            f"timed_out: {'yes' if timed_out else 'no'}\n"
            f"exit_code: {exit_code}\n"
            f"stdout:\n{evidence['stdout'] or '(empty)'}\n"
            f"stderr:\n{evidence['stderr'] or '(empty)'}"
        )


_REVIEW_CONTROL_PATHS = {
    ".bid",
    ".pytest_cache",
    "docs/task.md",
    "docs/todo.md",
    "docs/project-status.md",
    "docs/decisions.md",
    "docs/manager.md",
    "docs/worker.md",
    "docs/reviews",
}


def _review_path_blocked(rel_path):
    if rel_path in _REVIEW_CONTROL_PATHS:
        return True
    return (
        rel_path.startswith(".bid/")
        or rel_path.startswith(".pytest_cache/")
        or rel_path.startswith("__pycache__/")
        or rel_path.startswith("docs/reviews/")
    )


def _workspace_tree(root):
    tree = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in {".bid", ".pytest_cache", "__pycache__"}
        ]
        links = [directory for directory in dirs if os.path.islink(os.path.join(dirpath, directory))]
        dirs[:] = [directory for directory in dirs if directory not in links]
        for filename in files + links:
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if _review_path_blocked(rel):
                continue
            try:
                if os.path.islink(path):
                    tree[rel] = "SYMLINK (target not read): " + os.readlink(path)
                elif stat.S_ISREG(os.lstat(path).st_mode):
                    with open(path, encoding="utf-8", newline="") as file:
                        tree[rel] = file.read()
                else:
                    tree[rel] = "SPECIAL FILE (contents not read)"
            except (OSError, UnicodeDecodeError):
                pass
    return tree


def _text_file_metadata(text):
    trailing = text[len(text.rstrip("\r\n")):]
    count = len(re.findall(r"\r\n|\r|\n", trailing))
    ending = (
        "CRLF" if text.endswith("\r\n") else
        "LF" if text.endswith("\n") else
        "CR" if text.endswith("\r") else "none"
    )
    return f"bytes={len(text.encode('utf-8'))};end={ending};trailing={count}"


def _workspace_diff(base_root, candidate_root, limit=12000):
    base = _workspace_tree(base_root)
    cand = _workspace_tree(candidate_root)
    changes = []
    for rel in sorted(set(base) | set(cand)):
        before = base.get(rel)
        after = cand.get(rel)
        if before == after:
            continue
        if before is None:
            status, detail = "added", after
        elif after is None:
            status, detail = "deleted", before
        else:
            status = "modified"
            detail = "\n".join(difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
            ))
        changes.append((status, rel, detail or ""))

    if not changes:
        return "(no file changes)"

    def candidate_metadata(rel):
        if rel not in cand:
            return "candidate absent"
        if os.path.islink(os.path.join(candidate_root, rel)):
            return "symlink; target not read"
        if not stat.S_ISREG(os.lstat(os.path.join(candidate_root, rel)).st_mode):
            return "special file; contents not read"
        text = cand[rel]
        ending = "CRLF" if text.endswith("\r\n") else "LF" if text.endswith("\n") else "none"
        return f"bytes={len(text.encode('utf-8'))};end={ending}"

    index = "## Changed files\n" + "\n".join(
        f"- {status} {rel} [{candidate_metadata(rel)}]" for status, rel, _ in changes
    )
    full = "\n\n".join(
        f"### {status} {rel}" + (f"\n{detail}" if detail else "")
        for status, rel, detail in changes
    )
    if len(index) + 2 + len(full) <= limit:
        return f"{index}\n\n{full}"

    # ponytail: fixed shares keep an early large file from hiding later changes.
    share = max(0, (limit - len(index) - 2 * len(changes)) // len(changes))
    chunks = [index]
    marker = "\n...[middle truncated for this file]...\n"
    for status, rel, detail in changes:
        header = f"### {status} {rel}"
        if not detail or len(header) + 1 + len(detail) <= share:
            chunks.append(header + (f"\n{detail}" if detail else ""))
            continue
        excerpt = max(0, share - len(header) - len(marker) - 1)
        if len(detail) > excerpt:
            head = excerpt // 2
            tail = excerpt - head
            detail = detail[:head] + marker + (detail[-tail:] if tail else "")
        chunks.append(f"{header}\n{detail}")
    return "\n\n".join(chunks)


def _workspace_listing(root, limit=12000):
    tree = _workspace_tree(root)
    lines = []
    for rel in sorted(tree):
        body = tree[rel]
        if body.startswith(("SYMLINK (", "SPECIAL FILE ")):
            metadata = "contents unavailable"
        else:
            raw = body
            metadata = _text_file_metadata(raw)
            body = raw.rstrip("\r\n")
        lines.append(f"### {rel} [{metadata}]")
        if body:
            lines.append(body[:500])
        lines.append("")
    text = "\n".join(lines).strip()
    return text[:limit] if text else "(no workspace files)"


class TaskReviewAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config, task_number, base_state=None, candidate_state=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self.base_state = base_state
        self.candidate_state = candidate_state
        self.obs = get_log(self.workspace)

    def run(self, backend):
        if hasattr(backend, "role"):
            backend.role = "reviewer"
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"verdict": "ERROR", "reason": "task not found", "task_number": self.task_number}

        task_md = _read(self.workspace, "docs/task.md")
        if not self.base_state or not re.fullmatch(r"s\d+", self.base_state):
            return {"verdict": "ERROR", "reason": "base state not found", "task_number": self.task_number}

        base_root = os.path.join(self.workspace, ".bid", "states", self.base_state)
        if not os.path.isdir(base_root):
            return {"verdict": "ERROR", "reason": f"base state {self.base_state} not found", "task_number": self.task_number}

        if not self.candidate_state or not re.fullmatch(r"s\d+", self.candidate_state):
            return {"verdict": "ERROR", "reason": "candidate state not found", "task_number": self.task_number}
        candidate_root = os.path.join(self.workspace, ".bid", "states", self.candidate_state)
        if not os.path.isdir(candidate_root):
            return {"verdict": "ERROR", "reason": f"candidate state {self.candidate_state} not found", "task_number": self.task_number}
        diff_text = _workspace_diff(base_root, candidate_root)
        candidate_id = self.candidate_state

        prompt = (
            "# Review Assignment\n\n"
            f"Original request:\n{task_md}\n\n"
            f"Task:\n{task['description']}\n\n"
            f"Fixed base: {self.base_state}\n"
             f"Submitted candidate: {candidate_id}\n\n"
             f"Base -> candidate diff:\n{diff_text}\n\n"
             f"Candidate workspace snapshot:\n{_workspace_listing(candidate_root)}\n\n"
             "No harness-owned verification was executed against this submitted candidate.\n\n"
             "Judge only whether the submitted candidate satisfies the stated current task. "
             "Use the original request only as background constraints. "
             "Unfinished later checklist tasks are not grounds for REWORK.\n\n"
             "The candidate may complete the task through any lawful means: source-code "
             "modifications, test additions, documentation, configuration, deletion, or "
             "other changes. Do not require production-source modifications unless the "
             "task itself explicitly demands them. Return REWORK only for a concrete unmet "
             "task requirement or candidate defect — not because you prefer a different "
             "implementation approach.\n\n"
            "Return exactly one of:\n\n"
            "ACCEPT\n"
            "Reason: ...\n\n"
            "REWORK\n"
            "Reason: ..."
        )

        messages = [
            {"role": "system", "content": TASK_REVIEWER_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(self.RETRY_LIMIT):
            req_tok = self.obs.start(
                "model_request", role="reviewer", task=f"T{self.task_number}",
                base_state=self.base_state, candidate_state=self.candidate_state,
                retry=attempt,
            )
            try:
                response = _run_backend(
                    backend, messages, [], self.config.get("max_tokens", 32768),
                    grammar=protocol.grammar_for(self.config, "reviewer"),
                )
            except Exception as exc:
                self.obs.end(req_tok, error=str(exc))
                return {"verdict": "ERROR", "reason": f"model request failed: {exc}", "task_number": self.task_number}
            usage = response.get("usage") or {}
            self.obs.end(
                req_tok,
                finish_reason=response.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )

            content = (response.get("content") or "").strip()
            raw = content
            content = _clean_fences(content)

            result = self._parse(content)
            if result:
                result["task_number"] = self.task_number
                return result

            messages.append({"role": "assistant", "content": raw or "[no output]"})
            messages.append({"role": "user", "content": "No valid reviewer verdict was found. Return only:\nACCEPT followed by Reason:, or REWORK followed by Reason:."})

        return {"verdict": "ERROR", "reason": "failed to produce valid review after retries", "task_number": self.task_number}

    @staticmethod
    def _parse(content):
        first = content.strip().split("\n")[0].strip()
        if first not in ("ACCEPT", "REWORK"):
            return None
        reason = ""
        m = re.search(r"Reason:\s*(.*)", content, re.DOTALL)
        if m:
            reason = m.group(1).strip()
        if not reason:
            return None
        return {"verdict": first, "reason": reason}


RECONCILIATION_SYSTEM = """You are BID Manager performing project reconciliation.

Choose a decision from the original request, checklist, Reviewer verdict, and
each candidate's workspace snapshot and diff. Return only the needed sections,
in this order, ending with the mandatory project section:

- `# Done` on its own line, then `- Tn` for each accepted provisional task.
- `# Rework` on its own line, then `- Tn — reason` for one rejected task.
- `# Add` on its own line, then `- distinct task` for an omitted deliverable.
- `# Replace Remaining Plan` on its own line, then `- [ ] task` for each replacement unchecked task.
- `# Project` on its own line, then exactly `CONTINUE` or `COMPLETE`.

Example:
# Done
- T1
# Project
COMPLETE

Omit optional sections with no entries. Do not return empty headings.

Rules:
- Use only task IDs listed under Provisional submissions; checked [x] checklist
  tasks are already ratified and must not appear in # Done or # Rework.
- Put each provisional task in exactly one of # Done or # Rework. Never list a
  task in both. Without # Rework, every active provisional task must be in
  # Done. With # Rework Tn, earlier active provisional tasks go in # Done; Tn
  and later provisional tasks are invalidated and must not be in # Done.
- If the same task appears in both sections, remove it from one section. Choose
  which section to keep by comparing the candidate evidence with the request.
- # Rework contains exactly one task with a concrete unmet requirement.
- # Add only a distinct deliverable required by the original request but absent
  from the checklist. Never repeat the request or an existing task in # Add.
- Use # Replace Remaining Plan only when unchecked future tasks already exist;
  never use it to restate provisional or DONE tasks. It cannot coexist with
  # Rework or # Add.
- A Reviewer verdict is evidence, not an instruction to ignore the candidate
  snapshot or diff. Resolve any conflict from the artifact evidence.
- Choose #Project after applying your own sections. Use CONTINUE only if
  executable unchecked work remains. Use COMPLETE only when every deliverable
  is satisfied and no unchecked or provisional tasks remain.
- If no unchecked checklist tasks remain and all provisional tasks are accepted,
  choose COMPLETE.

Return only the sections. No commentary, analysis, or code fences."""


class ManagerReconcileAdapter:
    """Manager reconciles the provisional batch into its final disposition."""

    RETRY_LIMIT = 3

    def __init__(self, config, task_md, todo_text, evidence):
        self.config = config
        self.workspace = config["workspace"]
        self.task_md = task_md
        self.todo_text = todo_text
        self.evidence = evidence
        self.obs = get_log(self.workspace)
        self._last_raw = ""

    def run_once(self, backend, correction=None):
        """One model call + structural parse. Returns (decision, error)."""
        if hasattr(backend, "role"):
            backend.role = "manager_reconcile"
        messages = [
            {"role": "system", "content": RECONCILIATION_SYSTEM},
            {"role": "user", "content": self._build_prompt()},
        ]
        if correction:
            messages.append({"role": "assistant", "content": self._last_raw or "[no output]"})
            messages.append({"role": "user", "content": correction})

        req_tok = self.obs.start("model_request", role="manager_reconcile", retry=0)
        try:
            response = _run_backend(
                backend, messages, [], self.config.get("max_tokens", 32768),
                grammar=protocol.grammar_for(self.config, "manager_reconcile"),
            )
        except Exception as exc:
            self.obs.end(req_tok, error=str(exc))
            return None, f"model request failed: {exc}"
        usage = response.get("usage") or {}
        self.obs.end(
            req_tok,
            finish_reason=response.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

        content = (response.get("content") or "").strip()
        self._last_raw = content
        content = _clean_fences(content)
        return self._parse(content)

    def _build_prompt(self):
        parts = [
            "# Manager Reconciliation\n\n",
            f"Original request:\n{self.task_md}\n\n",
            f"Current checklist:\n{self.todo_text}\n\n",
            "Provisional submissions:\n\n",
            f"{self.evidence}\n\n" if self.evidence else "(no provisional submissions)\n\n",
            "Return your reconciliation decision.",
        ]
        return "".join(parts)

    @staticmethod
    def _parse(content):
        if not content or not content.strip():
            return None, "no output"
        decision = {"done": [], "rework": None, "add": [], "replace": None, "project": None}
        current = None
        seen_project = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("# "):
                header = stripped[2:].strip()
                if seen_project:
                    return None, f"unrecognized text after # Project: {stripped}"
                if header == "Done":
                    current = "done"
                elif header == "Rework":
                    current = "rework"
                elif header == "Add":
                    current = "add"
                elif header == "Replace Remaining Plan":
                    current = "replace"
                elif header == "Project":
                    current = "project"
                    seen_project = True
                else:
                    return None, f"unrecognized section: {stripped}"
                continue
            if current is None:
                return None, f"text outside a section: {stripped}"
            if current == "project":
                if stripped not in ("CONTINUE", "COMPLETE"):
                    return None, f"invalid # Project value: {stripped}"
                decision["project"] = stripped
                continue
            if not stripped.startswith("- "):
                return None, f"malformed item: {stripped}"
            item = stripped[2:].strip()
            if current == "done":
                match = re.fullmatch(r"(T\d+)\b\s*(.*)", item)
                if not match:
                    return None, f"malformed # Done item: {stripped}"
                decision["done"].append(int(match.group(1)[1:]))
            elif current == "rework":
                match = re.fullmatch(r"(T\d+)\b\s*[—–-]\s*(.+)", item)
                if not match or not match.group(2).strip():
                    return None, f"malformed # Rework item (need reason): {stripped}"
                if decision["rework"] is not None:
                    return None, "# Rework must contain exactly one task"
                decision["rework"] = {
                    "task": int(match.group(1)[1:]),
                    "reason": match.group(2).strip(),
                }
            elif current == "add":
                decision["add"].append(item)
            elif current == "replace":
                match = re.fullmatch(r"\[[ x-]\]\s*(?:T\d+\b\s*[—–-]\s*)?(.*)", item)
                description = match.group(1).strip() if match else item
                if not description:
                    return None, f"malformed # Replace Remaining Plan item: {stripped}"
                if decision["replace"] is None:
                    decision["replace"] = []
                decision["replace"].append(description)
        if decision["project"] is None:
            return None, "missing # Project section"
        return decision, None
