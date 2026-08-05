import difflib
import hashlib
import os
import shlex
import re
import subprocess
import sys
import shutil
import tempfile
import time

from . import permissions
from . import repo_context as repo_context_mod
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


RUN_OUTPUT_LIMIT = 2000

CONTROL_ROOTS = tuple(sorted(permissions.CONTROL_ROOTS))

TASK_REVIEWER_SYSTEM = """You are BID Task Reviewer.
Judge whether the candidate satisfies the assigned task using only the
provided original request, task, and fixed-base-to-candidate diff.

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
    if cmd["type"] == "MAP":
        return f"MAP {cmd.get('path', '')}".rstrip()
    if cmd["type"] == "FIND":
        return f"FIND {cmd.get('query', '')}".rstrip()
    return cmd["type"]


def _snapshot_control_state(workspace):
    snapshot = tempfile.mkdtemp(prefix="bid-control-")
    for rel in CONTROL_ROOTS:
        source = os.path.join(workspace, rel)
        if not os.path.exists(source):
            continue
        target = os.path.join(snapshot, rel)
        if os.path.isdir(source):
            shutil.copytree(source, target)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)
    return snapshot


def _entry_signature(path):
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
        elif os.path.exists(target):
            os.remove(target)

    for rel in CONTROL_ROOTS:
        source = os.path.join(snapshot, rel)
        if not os.path.exists(source):
            continue
        target = os.path.join(workspace, rel)
        if os.path.isdir(source):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copytree(source, target)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)


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
_REPO_CONTEXT_NOTICE = (
    "Repository tools are available: use MAP for a bounded filesystem view "
    "and FIND <literal> for exhaustive fixed-literal search."
)
_REPO_CONTEXT_ORIENTATION_FOOTER = (
    "Use MAP to inspect a narrower directory and FIND to locate a fixed literal.\n"
    "Do not repeat the same MAP or FIND request without changing the query or scope."
)
_FIND_LITERAL_GUIDANCE = (
    "FIND is fixed-literal, not regex. Characters such as . * [ ] ^ $ and quotes "
    "are searched literally."
)


def _display_find_query(query, limit=160):
    displayed = []
    used = 0
    for index, char in enumerate(query):
        if char.isprintable():
            escaped = char
        elif ord(char) <= 0xFF:
            escaped = f"\\x{ord(char):02x}"
        else:
            escaped = f"\\u{ord(char):04x}"
        if used + len(escaped) > limit:
            return "".join(displayed) + "..."
        displayed.append(escaped)
        used += len(escaped)
        if index + 1 >= len(query):
            break
    return "".join(displayed)


def _parse_content_into_turns(content, finish_reason=None, repo_context_mode="off"):
    # Keep the current finish_reason positional API while allowing compact
    # direct tests/callers to pass the mode as the second positional argument.
    if repo_context_mode == "off" and finish_reason in {"0", "1", "off", "tools", "inject"}:
        repo_context_mode = finish_reason
        finish_reason = None
    repo_tools = repo_context_mod.repo_context_tools_enabled(repo_context_mode)
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

        if repo_tools and stripped == "MAP":
            commands.append({"type": "MAP", "path": ""})
            i += 1
            continue

        if repo_tools and stripped.startswith("MAP "):
            commands.append({"type": "MAP", "path": stripped[4:].strip()})
            i += 1
            continue

        if repo_tools and stripped == "FIND":
            commands.append({"type": "FIND", "query": ""})
            i += 1
            continue

        if repo_tools and stripped.startswith("FIND "):
            commands.append({"type": "FIND", "query": stripped[5:]})
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


def _find_unknown_commands(content, commands, repo_context_mode="off"):
    repo_tools = repo_context_mod.repo_context_tools_enabled(repo_context_mode)
    lines = content.split("\n")
    consumed = set()
    li = 0
    while li < len(lines):
        s = lines[li].strip()
        if s == "Done" or s.startswith("SEARCH ") or s.startswith("READ ") or s.startswith("RUN "):
            consumed.add(li); li += 1; continue
        if repo_tools and (s == "MAP" or s.startswith("MAP ") or s == "FIND" or s.startswith("FIND ")):
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
        manager_md = _read(self.workspace, "docs/manager.md")
        task_md = _read(self.workspace, "docs/task.md")

        messages = [
            {"role": "system", "content": manager_md},
            {
                "role": "user",
                "content": (
                    f"# Task\n\n{task_md}\n\n"
                    "Create a checklist for this task. Return only unchecked Markdown checklist lines:\n\n"
                    "- [ ] Description\n"
                    "- [ ] Description\n\n"
                    "Keep the steps natural. If a step is a deliberate no-op, that is fine.\n\n"
                ),
            },
        ]

        for attempt in range(self.RETRY_LIMIT):
            req_tok = self.obs.start("model_request", role="manager", retry=attempt)
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 32768))
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
                        "- [ ] Description\n"
                        "- [ ] Description\n\n"
                        "No commentary. No code fences. Got:\n\n"
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
        self.repo_context_mode = repo_context_mod.normalize_mode(
            config.get("repo_context_mode", "off")
        )
        self._search_provider = search_provider or search_mod.create_provider(config)
        self._search_cache = search_mod.SearchCache(self.workspace)
        self._search_count = 0
        self._search_limit = config.get("max_searches_per_worker", 10)
        self._find_attempts = 0
        self._find_limit = config.get("repo_context_max_finds", 32)
        self._find_ledger = {}
        self._last_find_useful = False
        self._cache_hits = 0
        self.feedback = feedback or ""
        self._run_evidence = []
        self._implicit_write_count = 0
        self.obs = get_log(self.workspace)
        self._repo_context = None
        if repo_context_mod.repo_context_tools_enabled(self.repo_context_mode):
            self._repo_context = repo_context_mod.RepositoryContext(
                self.workspace,
                max_chars=config.get("repo_context_max_chars", 12000),
                max_find_hits=config.get("repo_context_max_find_hits", 100),
                max_file_bytes=config.get("repo_context_max_file_bytes", 1048576),
            )
        self._read_ledger = {}
        self._fully_read_paths = set()
        self._repo_event_entries = []

    def _worker_result(self, status, **fields):
        result = {"status": status}
        result.update(fields)
        if self._context_tools_enabled():
            result["observability_events"] = list(self._repo_event_entries)
        return result

    def _context_tools_enabled(self):
        return repo_context_mod.repo_context_tools_enabled(self.repo_context_mode)

    def _context_injection_enabled(self):
        return repo_context_mod.repo_context_injection_enabled(self.repo_context_mode)

    def _record_context_event(self, name, duration=None, **metadata):
        if not self._context_tools_enabled():
            return
        metadata["mode"] = self.repo_context_mode
        if duration is not None:
            metadata["duration_ms"] = round(max(0.0, duration) * 1000, 3)
        for key, value in list(metadata.items()):
            if isinstance(value, str):
                metadata[key] = value[:160]
        entry = self.obs.event(name, **metadata)
        self._repo_event_entries = getattr(self, "_repo_event_entries", [])
        self._repo_event_entries.append(entry)

    def _refresh_repo_context(self):
        started = time.monotonic()
        try:
            stats = self._repo_context.refresh()
        except Exception:
            self._record_context_event(
                "repo_context_refresh",
                time.monotonic() - started,
                ok=False,
                error="refresh failed",
            )
            return None
        self._record_context_event(
            "repo_context_refresh",
            time.monotonic() - started,
            ok=True,
            **stats,
        )
        return stats

    def _render_orientation(self):
        started = time.monotonic()
        stats = self._refresh_repo_context()
        if stats is None:
            self._record_context_event(
                "repo_context_map",
                time.monotonic() - started,
                ok=False,
                entry_count=0,
                result_count=0,
                truncated=False,
                source="orientation",
            )
            return "Repository context warning: workspace index refresh failed."

        map_result = self._repo_context.map_result(refresh=False)
        prefix = "# Workspace orientation\n\n"
        suffix = f"\n\n{_REPO_CONTEXT_ORIENTATION_FOOTER}\n\n{_REPO_CONTEXT_NOTICE}"
        max_chars = max(0, int(self.config.get("repo_context_max_chars", 12000)))
        map_budget = max_chars - len(prefix) - len(suffix)
        if map_budget <= 0:
            self._record_context_event(
                "repo_context_map",
                time.monotonic() - started,
                ok=True,
                scope=".",
                entry_count=map_result["entry_count"],
                result_count=map_result["entry_count"],
                truncated=True,
                source="orientation",
            )
            return (prefix + suffix)[:max_chars]
        bounded_map = repo_context_mod.render_workspace_map(
            self._repo_context.index,
            max_chars=map_budget,
        )
        self._record_context_event(
            "repo_context_map",
            time.monotonic() - started,
            ok=True,
            scope=".",
            entry_count=map_result["entry_count"],
            result_count=map_result["entry_count"],
            truncated=repo_context_mod._is_truncated(bounded_map),
            source="orientation",
        )
        return (prefix + bounded_map + suffix)[:max_chars]

    def _startup_context_text(self):
        if not self._context_tools_enabled():
            return ""
        if self._context_injection_enabled():
            return self._render_orientation()
        if self._refresh_repo_context() is None:
            return _REPO_CONTEXT_NOTICE + "\nRepository context warning: workspace index refresh failed."
        return _REPO_CONTEXT_NOTICE

    def _reset_context_text(self):
        if not self._context_tools_enabled():
            return ""
        if self._context_injection_enabled():
            return self._render_orientation()
        return _REPO_CONTEXT_NOTICE

    def _map_command(self, requested_path):
        started = time.monotonic()
        safe, err, rel = permissions.check_path_safety(requested_path or ".", self.workspace)
        if not safe:
            result = f"error: {err}"
            self._record_context_event(
                "repo_context_map", time.monotonic() - started, ok=False,
                entry_count=0, result_count=0, truncated=False, source="command",
            )
            return result
        if rel != "." and repo_context_mod.is_excluded_path(rel):
            result = f"error: path is excluded from repository context: {requested_path}"
            self._record_context_event(
                "repo_context_map", time.monotonic() - started, ok=False,
                entry_count=0, result_count=0, truncated=False, source="command",
            )
            return result
        absolute = os.path.join(self.workspace, rel)
        if not os.path.exists(absolute):
            result = f"error: path not found: {requested_path or '.'}"
            self._record_context_event(
                "repo_context_map", time.monotonic() - started, ok=False,
                entry_count=0, result_count=0, truncated=False, source="command",
            )
            return result
        if not os.path.isdir(absolute):
            result = f"error: not a directory: {requested_path or '.'}"
            self._record_context_event(
                "repo_context_map", time.monotonic() - started, ok=False,
                entry_count=0, result_count=0, truncated=False, source="command",
            )
            return result
        if self._refresh_repo_context() is None:
            result = "error: repository context refresh failed"
            self._record_context_event(
                "repo_context_map", time.monotonic() - started, ok=False,
                entry_count=0, result_count=0, truncated=False, source="command",
            )
            return result
        scope = None if rel == "." else rel
        map_result = self._repo_context.map_result(scope=scope, refresh=False)
        self._record_context_event(
            "repo_context_map", time.monotonic() - started, ok=True,
            scope=scope or ".", entry_count=map_result["entry_count"],
            result_count=map_result["entry_count"], truncated=map_result["truncated"],
            source="command",
        )
        return map_result["output"]

    def _find_command(self, query):
        self._find_attempts += 1
        self._last_find_useful = False
        attempt = self._find_attempts
        query_hash = hashlib.sha256(query.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        if attempt > self._find_limit:
            result = f"error: FIND limit ({self._find_limit}) reached"
            self._record_context_event(
                "repo_context_find",
                ok=False,
                outcome="budget_rejected",
                attempt=attempt,
                limit=self._find_limit,
                query_hash=query_hash,
                query_length=len(query),
                result_count=0,
                total_matches=0,
                truncated=False,
                source="command",
            )
            return result

        started = time.monotonic()
        if self._refresh_repo_context() is None:
            result = "error: repository context refresh failed"
            self._record_context_event(
                "repo_context_find", time.monotonic() - started, ok=False,
                outcome="error",
                attempt=attempt,
                query_hash=query_hash,
                query_length=len(query), result_count=0, truncated=False, source="command",
            )
            return result

        index_state = repo_context_mod.search_state_fingerprint(self._repo_context.index)
        previous = self._find_ledger.get(query)
        if previous and previous["index_state"] == index_state:
            result = (
                "unchanged: identical FIND query was already answered for the current repository state\n"
                f"query: {_display_find_query(query)}\n"
                f"previous matches: {previous['matches']}"
            )
            self._record_context_event(
                "repo_context_find",
                time.monotonic() - started,
                outcome="deduplicated",
                attempt=attempt,
                query_hash=query_hash,
                query_length=len(query),
                index_state=index_state,
                ok=previous.get("ok", True),
                result_count=previous["matches"],
                total_matches=previous["matches"],
                truncated=False,
                source="command",
            )
            return result

        search_result = self._repo_context.find_result(query, refresh=False)
        result = search_result["output"]
        if (
            search_result["ok"]
            and search_result["total_matches"] == 0
            and any(char in query for char in ".*[]^$'\"|?+()\\")
        ):
            result += "\n" + _FIND_LITERAL_GUIDANCE
        self._find_ledger[query] = {
            "index_state": index_state,
            "matches": search_result["total_matches"],
            "ok": search_result["ok"],
        }
        self._last_find_useful = search_result["ok"]
        self._record_context_event(
            "repo_context_find", time.monotonic() - started,
            ok=search_result["ok"],
            outcome="executed",
            attempt=attempt,
            query_hash=query_hash,
            index_state=index_state,
            query_length=len(query),
            result_count=len(search_result["hits"]),
            total_matches=search_result["total_matches"],
            truncated=search_result["truncated"],
            skipped_binary=search_result["skipped"].get("binary", 0),
            skipped_oversized=search_result["skipped"].get("oversized", 0),
            skipped_unreadable=search_result["skipped"].get("unreadable", 0),
            source="command",
        )
        return result

    def run(self, backend):
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return self._worker_result(
                "error", reason=f"T{self.task_number} not found in TODO"
            )

        worker_policy = _read(self.workspace, "docs/worker.md")
        task_prompt = (
            f"\nTask T{self.task_number}: {task['description']}\n\n"
        )
        if self.feedback:
            task_prompt += f"\nPrevious reviewer feedback:\n{self.feedback}\n"
        base_task_prompt = task_prompt
        startup_context = self._startup_context_text()
        if startup_context:
            task_prompt += "\n\n" + startup_context

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
        hard_ceiling = self.config.get("worker_timeout", 3600)
        inactivity_timeout = self.config.get("inactivity_timeout", 600)
        repeat_limit = self.config.get("repeat_action_limit", 5)
        session_start = time.monotonic()
        soft_resets = 0
        last_sig = None
        turn_repeat = 0
        _read_tracker = {}  # canonical_rel_path → (useful_count, last_hash)
        self._read_ledger.clear()
        self._fully_read_paths.clear()

        while time.monotonic() - session_start < hard_ceiling:
            req_tok = self.obs.start("model_request", role="worker", task=f"T{self.task_number}", attempt=1)
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 32768))
            except Exception as exc:
                self.obs.end(req_tok, error=str(exc))
                return self._worker_result(
                    "error", reason=f"model request failed: {exc}"
                )
            usage = response.get("usage") or {}
            self.obs.end(
                req_tok,
                finish_reason=response.get("finish_reason"),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )

            raw_content = response.get("content") or ""
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
                commands = _parse_content_into_turns(
                    content, finish_reason, self.repo_context_mode
                )
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
                no_command_hint = (
                    "No executable BID command was found. Respond only with actual READ, WRITE, RUN, or Done commands. Do not explain or describe the commands."
                )
                if self._context_tools_enabled():
                    no_command_hint = (
                        "No executable BID command was found. Respond only with actual READ, MAP, FIND, WRITE, RUN, or Done commands. Do not explain or describe the commands."
                    )
                messages.append({
                    "role": "user",
                    "content": no_command_hint
                })
            else:
                for cmd in commands:
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
                                        result = _read(self.workspace, rel)
                                        fhash = _hash_file(abs_path)
                                        if self._context_tools_enabled():
                                            ledger_key = (rel, fhash)
                                            if fhash != "?" and ledger_key in self._read_ledger:
                                                result = (
                                                    f"unchanged: {rel} was already supplied earlier in this Worker context\n"
                                                    f"sha256: {fhash[:12]}"
                                                )
                                                self._record_context_event(
                                                    "repo_context_read_deduplicated",
                                                    path=rel, sha256=fhash[:12], count=1,
                                                )
                                            else:
                                                if fhash != "?":
                                                    for key in list(self._read_ledger):
                                                        if key[0] == rel:
                                                            del self._read_ledger[key]
                                                    self._read_ledger[ledger_key] = True
                                                useful = True
                                                first_path_read = rel not in self._fully_read_paths
                                                self._fully_read_paths.add(rel)
                                                self._record_context_event(
                                                    "repo_context_read",
                                                    path=rel, read_count=1,
                                                    full_read=True,
                                                    unique_file=first_path_read,
                                                )
                                        else:
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

                    if cmd["type"] == "MAP":
                        result = self._map_command(cmd.get("path", ""))
                        if not result.startswith("error:"):
                            useful = True
                        sig = f"MAP {cmd.get('path', '')}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        _log_worker_event(self._vc, "worker result", result)
                        messages.append({"role": "user", "content": result})
                        self.obs.end(cmd_tok)
                        continue

                    if cmd["type"] == "FIND":
                        result = self._find_command(cmd.get("query", ""))
                        if self._last_find_useful:
                            useful = True
                        sig = f"FIND {cmd.get('query', '')}|{result[:50]}"
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
                        if self._search_count >= self._search_limit:
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
                                useful = True
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
                                result = self._run_command(cmd["command"])
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
                            useful = True
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

            # Unknown-command feedback
            unknown_cmds = _find_unknown_commands(
                raw_content, commands, self.repo_context_mode
            )
            if unknown_cmds:
                allowed = "READ, WRITE, RUN, Done"
                if self._context_tools_enabled():
                    allowed = "READ, MAP, FIND, WRITE, RUN, Done"
                err_msg = ("error: unknown command(s): " + ", ".join(unknown_cmds[:3])
                           + f"\nAllowed commands are {allowed}."
                           + "\nTo inspect directories, use RUN ls.")
                messages.append({"role": "user", "content": err_msg})
                _log_worker_event(self._vc, "worker result", err_msg)
                # NOT counted as useful or changed; does NOT reset repeat

            # Done processing
            if policy_violation or fence_violation or replace_failed:
                saw_done = False

            if saw_done:
                return self._worker_result(
                    "done", checked=False, run_evidence=list(self._run_evidence)
                )

            # Soft reset on repeat stall
            if turn_repeat >= repeat_limit and not changed and not useful:
                soft_resets += 1
                if soft_resets > self.MAX_SOFT_RESETS:
                    return self._worker_result(
                        "stalled", reason="repeated action without progress"
                    )
                system = messages[0]
                self._read_ledger.clear()
                self._fully_read_paths.clear()
                reset_context = self._reset_context_text()
                reset_prompt = base_task_prompt
                if reset_context:
                    reset_prompt += "\n\n" + reset_context
                messages = [
                    system,
                    {
                        "role": "user",
                        "content": (
                            f"{reset_prompt}\n\n"
                            f"[ERROR: did not make progress. "
                            f"Last command: {last_sig.split('|')[0] if '|' in last_sig else last_sig}. "
                            f"Try a different approach.]"
                        ),
                    },
                ]
                observer = Observer(self.workspace, self.task_number)
                last_sig = None
                turn_repeat = 0
                continue

            # Activity marking
            if useful or changed:
                observer.mark_activity()

            if observer.inactive_for() > inactivity_timeout:
                return self._worker_result(
                    "timeout", reason=f"inactive {observer.inactive_for():.0f}s"
                )

        return self._worker_result("timeout", reason=f"hard ceiling {hard_ceiling}s")

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
        return f"wrote {len(content)} bytes to {rel}"

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

    def _run_command(self, command):
        t0 = time.monotonic()
        try:
            return self._run_command_inner(command)
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

    def _run_command_inner(self, command):
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

        timeout = self.config.get("run_timeout", 60)
        try:
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
        for filename in files:
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if _review_path_blocked(rel):
                continue
            try:
                with open(path, encoding="utf-8") as file:
                    tree[rel] = file.read()
            except (OSError, UnicodeDecodeError):
                pass
    return tree


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

    index = "## Changed files\n" + "\n".join(
        f"- {status} {rel}" for status, rel, _ in changes
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
        lines.append(f"### {rel}")
        body = tree[rel].strip()
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
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"verdict": "ERROR", "reason": "task not found", "task_number": self.task_number}

        task_md = _read(self.workspace, "docs/task.md")
        if not self.base_state:
            return {"verdict": "ERROR", "reason": "base state not found", "task_number": self.task_number}

        base_root = os.path.join(self.workspace, ".bid", "states", self.base_state)
        if not os.path.isdir(base_root):
            return {"verdict": "ERROR", "reason": f"base state {self.base_state} not found", "task_number": self.task_number}

        diff_text = _workspace_diff(base_root, self.workspace)
        candidate_id = self.candidate_state or "(unknown)"

        prompt = (
            "# Review Assignment\n\n"
            f"Original request:\n{task_md}\n\n"
            f"Task:\n{task['description']}\n\n"
            f"Fixed base: {self.base_state}\n"
            f"Submitted candidate: {candidate_id}\n\n"
            f"Base -> candidate diff:\n{diff_text}\n\n"
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
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 32768))
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

Provisional Worker submissions await your decision. Return ONE batch decision
using only these sections, in any order, ending with the mandatory project section:

# Done
- T1
- T2

# Rework
- T3 — reason for rework

# Add
- new task description

# Replace Remaining Plan
- [ ] T1 — new description
- [ ] T2 — new description

# Project
CONTINUE

Rules:
- Without # Rework, every active provisional task must appear in # Done.
- With # Rework Tn, every earlier active provisional task must appear in
  # Done; Tn and every later provisional task are automatically invalidated,
  and no task at or after Tn may appear in # Done.
- # Rework contains exactly one task with a non-empty reason.
- # Done, # Rework and # Add may coexist.
- # Replace Remaining Plan may coexist with # Done but not with # Rework or
  # Add; it changes only currently unchecked future tasks, never provisional
  or DONE tasks.
- # Project must be exactly CONTINUE or COMPLETE.
- CONTINUE is valid only when unchecked work remains after application.
- COMPLETE is valid only when every task is DONE after application.

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
        messages = [
            {"role": "system", "content": RECONCILIATION_SYSTEM},
            {"role": "user", "content": self._build_prompt()},
        ]
        if correction:
            messages.append({"role": "assistant", "content": self._last_raw or "[no output]"})
            messages.append({"role": "user", "content": correction})

        req_tok = self.obs.start("model_request", role="manager_reconcile", retry=0)
        try:
            response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 32768))
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
