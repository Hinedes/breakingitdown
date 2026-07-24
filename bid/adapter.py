import difflib
import hashlib
import os
import re
import time

from . import permissions
from . import search as search_mod
from . import todo as todo_mod
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


# ── Command parsing ──────────────────────────────────────────────────

def _parse_content_into_turns(content):
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
            if not terminated:
                # Unterminated WRITE: reject, perform no write
                commands.append({"type": "WRITE_UNTERMINATED", "path": path})
                continue
            commands.append({"type": "WRITE", "path": path, "content": "\n".join(body_lines)})
            continue

        i += 1

    return commands
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

    def run(self, backend):
        manager_md = _read(self.workspace, "docs/manager.md")
        task_md = _read(self.workspace, "docs/task.md")

        messages = [
            {"role": "system", "content": manager_md},
            {
                "role": "user",
                "content": (
                    f"# Task\n\n{task_md}\n\n"
                    "Create a numbered checklist for this task. Return only Markdown checklist lines.\n"
                    "Keep the steps natural. If a step is a deliberate no-op, that is fine.\n\n"
                    "Example:\n"
                    "- [ ] T1 — Short description\n"
                    "- [ ] T2 — Short description"
                ),
            },
        ]

        for attempt in range(self.RETRY_LIMIT):
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"status": "error", "reason": f"model request failed: {exc}"}

            content = (response.get("content") or "").strip()
            content = _clean_fences(content)

            if self._valid(content):
                _write(self.workspace, "docs/todo.md", content)
                return {"status": "success", "todo": content}

            if attempt < self.RETRY_LIMIT - 1:
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": (
                        "Return only checklist lines in this exact format:\n"
                        "- [ ] T1 — Description\n"
                        "- [ ] T2 — Description\n\n"
                        "No commentary. No code fences. Got:\n\n"
                        + content[:500]
                    ),
                })

        return {"status": "error", "reason": "failed to produce valid TODO after 3 attempts"}

    @staticmethod
    def _valid(text):
        if not text:
            return False
        tasks = todo_mod.parse_todo(text)
        valid, _ = validate_todo_tasks(tasks)
        return valid


class WorkerAdapter:
    MAX_SOFT_RESETS = 3

    def __init__(self, config, task_number, search_provider=None, feedback=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self._search_provider = search_provider or search_mod.create_provider(config)
        self._search_cache = search_mod.SearchCache(self.workspace)
        self._search_count = 0
        self._search_limit = config.get("max_searches_per_worker", 10)
        self._cache_hits = 0
        self.feedback = feedback or ""

    def run(self, backend):
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"status": "error", "reason": f"T{self.task_number} not found in TODO"}

        worker_policy = _read(self.workspace, "docs/worker.md")
        task_prompt = (
            f"\nTask T{self.task_number}: {task['description']}\n\n"
            "Only use these commands:\n"
            "  READ <path>     — read a file\n"
            "  SEARCH <query>  — search for current information\n"
            "  WRITE <path>    — write content; end with END WRITE on its own line\n"
            "  Done            — finish when the task is complete\n\n"
            "If the right answer is no file changes, say Done.\n"
        )
        if self.feedback:
            task_prompt += f"\nPrevious reviewer feedback:\n{self.feedback}\n"

        messages = [
            {"role": "system", "content": f"/no_think\n{worker_policy}"},
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

        while time.monotonic() - session_start < hard_ceiling:
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"status": "error", "reason": f"model request failed: {exc}"}

            content = (response.get("content") or "").strip()

            messages.append({"role": "assistant", "content": content or "[no output]"})
            changed = False
            useful = False
            saw_done = False

            if content:
                commands = _parse_content_into_turns(content)
            else:
                commands = []

            if not commands:
                sig = "no_commands"
                if sig == last_sig:
                    turn_repeat += 1
                else:
                    turn_repeat = 0
                last_sig = sig
                messages.append({
                    "role": "user",
                    "content": "Use READ <path>, SEARCH <query>, WRITE <path>\\n<content>\\nEND WRITE, or Done."
                })
            else:
                for cmd in commands:
                    if cmd["type"] == "Done":
                        saw_done = True
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
                        messages.append({"role": "user", "content": result})
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
                        messages.append({"role": "user", "content": result})
                        continue

                    if cmd["type"] == "WRITE":
                        try:
                            result = self._write_command(cmd["path"], cmd["content"])
                            if not result.startswith("error"):
                                useful = True
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
                        messages.append({"role": "user", "content": result})
                        continue

                    if cmd["type"] == "WRITE_UNTERMINATED":
                        result = f"error: WRITE {cmd['path']} must end with END WRITE on its own line"
                        sig = f"WRITE_UNTERMINATED"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        messages.append({"role": "user", "content": result})
                        continue

            # Done processing
            if saw_done:
                return {"status": "done", "checked": False}

            # Soft reset on repeat stall
            if turn_repeat >= repeat_limit and not changed and not useful:
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
                continue

            # Activity marking
            if useful or changed:
                observer.mark_activity()

            if observer.inactive_for() > inactivity_timeout:
                return {"status": "timeout", "reason": f"inactive {observer.inactive_for():.0f}s"}

        return {"status": "timeout", "reason": f"hard ceiling {hard_ceiling}s"}

    def _write_command(self, path, content):
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


_REVIEW_CONTROL_PATHS = {
    ".bid",
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
    return rel_path.startswith(".bid/") or rel_path.startswith("docs/reviews/")


def _workspace_tree(root):
    tree = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [directory for directory in dirs if directory != ".bid"]
        for filename in files:
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if _review_path_blocked(rel):
                continue
            try:
                with open(path, encoding="utf-8") as file:
                    tree[rel] = file.read()
            except OSError:
                pass
    return tree


def _workspace_diff(base_root, candidate_root, limit=12000):
    base = _workspace_tree(base_root)
    cand = _workspace_tree(candidate_root)
    chunks = []
    for rel in sorted(set(base) | set(cand)):
        before = base.get(rel)
        after = cand.get(rel)
        if before == after:
            continue
        if before is None:
            chunks.append(f"### added {rel}")
            if after:
                chunks.append(after[:2000])
            continue
        if after is None:
            chunks.append(f"### deleted {rel}")
            continue
        chunks.append(f"### modified {rel}")
        chunks.extend(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm="",
            )
        )
    text = "\n".join(chunks).strip()
    return text[:limit] if text else "(no file changes)"


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


def _research_context(workspace, task_number):
    research_dir = search_mod._research_dir(workspace, task_number)
    if not os.path.isdir(research_dir):
        return "", research_dir, False

    parts = ["", "## Supporting research", ""]
    for fname in sorted(os.listdir(research_dir)):
        fpath = os.path.join(research_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8") as file:
                content = file.read()
        except OSError:
            continue
        parts.append(f"### {fname}")
        parts.append(content[:500])
        parts.append("")
    return ("\n".join(parts).strip(), research_dir, True)


class TaskReviewAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config, task_number, base_state=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self.base_state = base_state

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
        research_context, research_dir, has_research = _research_context(self.workspace, self.task_number)
        if has_research and not search_mod.has_citations(diff_text, research_dir):
            return {
                "verdict": "REWORK",
                "reason": "artifact does not cite supporting research evidence",
                "task_number": self.task_number,
            }

        prompt = (
            "# Review Assignment\n\n"
            f"Original request:\n{task_md}\n\n"
            f"Task:\n{task['description']}\n\n"
            f"Base -> candidate diff:\n{diff_text}\n"
        )
        if research_context:
            prompt += f"{research_context}\n\n"
        prompt += (
            "Judge only whether the diff satisfies the request.\n\n"
            "Return exactly one of:\n\n"
            "ACCEPT\n"
            "Reason: ...\n\n"
            "REWORK\n"
            "Reason: ..."
        )

        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(self.RETRY_LIMIT):
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"verdict": "ERROR", "reason": f"model request failed: {exc}", "task_number": self.task_number}

            content = (response.get("content") or "").strip()
            raw = content
            content = _clean_fences(content)

            result = self._parse(content)
            if result:
                result["task_number"] = self.task_number
                return result

            messages.append({"role": "assistant", "content": raw or "[no output]"})
            messages.append({"role": "user", "content": "Return ACCEPT or REWORK with Reason."})

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


ArtifactReviewAdapter = TaskReviewAdapter


class CompletionReviewAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        task_md = _read(self.workspace, "docs/task.md")
        workspace_text = _workspace_listing(self.workspace)

        prompt = (
            "# Completion Review\n\n"
            f"Original request:\n{task_md}\n\n"
            f"Final workspace:\n{workspace_text}\n\n"
            "Return exactly one of:\n\n"
            "COMPLETE\n"
            "Reason: ...\n\n"
            "MISSING\n"
            "- missing deliverable\n"
            "- missing deliverable"
        )

        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(self.RETRY_LIMIT):
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"verdict": "ERROR", "reason": f"model request failed: {exc}"}

            content = (response.get("content") or "").strip()
            raw = content
            content = _clean_fences(content)

            result = self._parse(content)
            if result:
                return result

            messages.append({"role": "assistant", "content": raw or "[no output]"})
            messages.append({"role": "user", "content": "Return COMPLETE or MISSING."})

        return {"verdict": "ERROR", "reason": "failed to produce valid completion review"}

    @staticmethod
    def _parse(content):
        first = content.strip().split("\n")[0].strip()
        if first == "COMPLETE":
            return {"verdict": "COMPLETE", "missing": []}
        if first == "MISSING":
            missing = []
            seen = set()
            for line in content.split("\n"):
                m = re.match(r"^\s*[-*]\s+(.*)", line)
                if m:
                    item = m.group(1).strip()
                    key = item.lower()
                    if item and key not in seen:
                        seen.add(key)
                        missing.append(item)
            if missing:
                return {"verdict": "MISSING", "missing": missing}
        return None
