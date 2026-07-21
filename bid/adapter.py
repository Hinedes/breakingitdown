import hashlib
import os
import re
import tempfile
import time

from . import permissions
from . import search as search_mod
from . import todo as todo_mod
from .observer import Observer


def _safe_path(workspace, rel):
    safe, err, normalized = permissions.check_path_safety(rel, workspace)
    if not safe or normalized in (None, "."):
        raise ValueError(f"path safety violation: {err or 'workspace root denied'}")
    return os.path.join(workspace, normalized)


def _read(workspace, rel):
    path = _safe_path(workspace, rel)
    if not os.path.exists(path):
        return ""
    if not os.path.isfile(path):
        raise ValueError(f"not a file: {rel}")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _write(workspace, rel, content):
    path = _safe_path(workspace, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _clean_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _hash_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return "?"


def _parse_content_into_turns(content):
    lines = content.split("\n")
    commands = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped == "Done":
            commands.append({"type": "Done"})
            index += 1
            continue
        if stripped.startswith("SEARCH "):
            commands.append({"type": "SEARCH", "query": stripped[7:].strip()})
            index += 1
            continue
        if stripped.startswith("READ "):
            commands.append({"type": "READ", "path": stripped[5:].strip()})
            index += 1
            continue
        if stripped.startswith("WRITE "):
            path = stripped[6:].strip()
            index += 1
            body = []
            while index < len(lines) and lines[index].strip() != "END WRITE":
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                commands.append({"type": "WRITE_UNTERMINATED", "path": path})
                continue
            index += 1
            commands.append({"type": "WRITE", "path": path, "content": "\n".join(body)})
            continue
        index += 1
    return commands


def _build_scoped_manifest(workspace, task_number, output_path, input_paths):
    lines = ["# Available files", ""]
    candidates = ["docs/todo.md", "docs/worker.md"] + list(input_paths)
    for rel in candidates:
        try:
            path = _safe_path(workspace, rel)
        except ValueError:
            continue
        if os.path.exists(path):
            lines.append(f"- {rel}")
    if output_path:
        parent = os.path.dirname(output_path)
        if parent:
            lines.append(f"- {parent}/ (output directory)")
    research = search_mod._research_dir(workspace, task_number)
    if os.path.isdir(research):
        lines.append(f"- docs/research/T{task_number}/")
    return "\n".join(lines) + "\n"


def _is_reserved_control_path(rel):
    if rel == ".bid" or rel.startswith(".bid/"):
        return True
    if rel == "docs/reviews" or rel.startswith("docs/reviews/"):
        return True
    if rel == "docs/research" or rel.startswith("docs/research/"):
        return True
    if rel == "docs/.completed_hash":
        return True
    return rel in {
        "docs/task.md",
        "docs/todo.md",
        "docs/project-status.md",
        "docs/decisions.md",
        "docs/manager.md",
        "docs/worker.md",
    }


def validate_todo_tasks(tasks):
    if not tasks:
        return False, "no tasks"
    numbers = [task["number"] for task in tasks]
    if numbers != list(range(1, len(tasks) + 1)):
        return False, f"task numbers must be sequential T1..T{len(tasks)}, got {numbers}"
    outputs = set()
    for task in tasks:
        expected_id = f"T{task['number']}"
        if task["id"] != expected_id:
            return False, f"{task['id']} has noncanonical format (expected {expected_id})"
        if task["checked"]:
            return False, f"{task['id']} must start unchecked"
        if not task["description"].strip():
            return False, f"{task['id']} has empty description"
        output, inputs = todo_mod.get_task_metadata(tasks, task["number"])
        for value in [output] + inputs:
            if not value:
                return False, f"{task['id']} has empty path"
            safe, err, normalized = permissions.check_path_safety(value, "/dummy")
            if not safe or normalized in (None, "."):
                return False, f"{task['id']} has unsafe path: {err or 'workspace root'}"
            if _is_reserved_control_path(normalized):
                return False, f"{task['id']} path targets a protected file: {normalized}"
        _, _, normalized_output = permissions.check_path_safety(output, "/dummy")
        if normalized_output in outputs:
            return False, f"duplicate output path: {output}"
        outputs.add(normalized_output)
    return True, None


class ManagerInitAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {
                "role": "user",
                "content": (
                    f"# Task\n\n{_read(self.workspace, 'docs/task.md')}\n\n"
                    "Create a numbered checklist. Return only Markdown task lines, for example:\n"
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
            content = _clean_fences((response.get("content") or "").strip())
            if self._valid(content):
                _write(self.workspace, "docs/todo.md", content)
                return {"status": "success", "todo": content}
            if attempt < self.RETRY_LIMIT - 1:
                messages.extend([
                    {"role": "assistant", "content": content or "[no output]"},
                    {
                        "role": "user",
                        "content": (
                            "Return only checklist lines in this exact format:\n"
                            "- [ ] T1 — Description\n- [ ] T2 — Description\n"
                            "No commentary or code fences."
                        ),
                    },
                ])
        return {"status": "error", "reason": "failed to produce valid TODO after 3 attempts"}

    @staticmethod
    def _valid(text):
        if not text:
            return False
        valid, _ = validate_todo_tasks(todo_mod.parse_todo(text))
        return valid


class WorkerAdapter:
    MAX_SOFT_RESETS = 3

    def __init__(self, config, task_number, search_provider=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self._search_provider = search_provider or search_mod.create_provider(config)
        self._search_cache = search_mod.SearchCache(self.workspace)
        self._search_requests = 0
        self._search_limit = config.get("max_searches_per_worker", 10)
        self._cache_hits = 0

    def _assignment(self, task, output_path, input_paths, manifest):
        inputs = "Input files: " + ", ".join(input_paths) if input_paths else ""
        return (
            f"{manifest}\nTask T{self.task_number}: {task['description']}\n"
            f"Output file: {output_path}\n{inputs}\n\n"
            "Only use these commands:\n"
            "  READ <path>\n"
            "  SEARCH <query>\n"
            "  WRITE <path>\n<content>\nEND WRITE\n"
            f"  Done — only after T{self.task_number} is checked in docs/todo.md\n\n"
            "First READ docs/todo.md. Perform only this task. Write the artifact, preserve every "
            f"TODO line while changing only T{self.task_number} from [ ] to [x], then output Done."
        )

    @staticmethod
    def _repeat(last_signature, signature, repeat_count):
        return repeat_count + 1 if signature == last_signature else 1

    def run(self, backend):
        tasks = todo_mod.parse_todo(_read(self.workspace, "docs/todo.md"))
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"status": "error", "reason": f"T{self.task_number} not found in TODO"}
        output_path, input_paths = todo_mod.get_task_metadata(tasks, self.task_number)
        policy = _read(self.workspace, "docs/worker.md")
        manifest = _build_scoped_manifest(self.workspace, self.task_number, output_path, input_paths)
        assignment = self._assignment(task, output_path, input_paths, manifest)
        messages = [
            {"role": "system", "content": f"/no_think\n{policy}"},
            {"role": "user", "content": assignment},
        ]

        observer = Observer(self.workspace, self.task_number)
        hard_ceiling = self.config.get("worker_timeout", 3600)
        inactivity_timeout = self.config.get("inactivity_timeout", 600)
        repeat_limit = self.config.get("repeat_action_limit", 5)
        started = time.monotonic()
        soft_resets = 0
        done_without_check = 0
        last_signature = None
        repeat_count = 0
        read_hashes = {}

        while time.monotonic() - started < hard_ceiling:
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"status": "error", "reason": f"model request failed: {exc}"}
            content = (response.get("content") or "").strip()
            messages.append({"role": "assistant", "content": content or "[no output]"})
            commands = _parse_content_into_turns(content) if content else []
            useful = False
            changed = False
            saw_done = False

            if not commands:
                signature = "NO_COMMANDS"
                repeat_count = self._repeat(last_signature, signature, repeat_count)
                last_signature = signature
                messages.append({
                    "role": "user",
                    "content": "Use READ <path>, SEARCH <query>, WRITE <path> followed by END WRITE, or Done.",
                })

            for command in commands:
                kind = command["type"]
                if kind == "Done":
                    saw_done = True
                    continue

                if kind == "READ":
                    raw_path = command["path"]
                    absolute = None
                    rel = raw_path
                    safe, err, normalized = permissions.check_path_safety(raw_path, self.workspace)
                    if not safe or normalized in (None, "."):
                        result = f"error: {err or 'workspace root denied'}"
                        signature = f"READ-ERROR:{raw_path}:{result}"
                    else:
                        rel = normalized
                        absolute = os.path.join(self.workspace, rel)
                        allowed, reason = permissions.check_read_permission(rel, permissions.ROLE_WORKER)
                        if not allowed:
                            result = f"error: permission denied: {reason}"
                        elif not os.path.exists(absolute):
                            result = f"file not found: {raw_path}"
                        elif not os.path.isfile(absolute):
                            result = f"not a file: {raw_path}"
                        else:
                            result = _read(self.workspace, rel)
                            file_hash = _hash_file(absolute)
                            if read_hashes.get(rel) != file_hash:
                                useful = True
                            read_hashes[rel] = file_hash
                        identity = _hash_file(absolute) if absolute and os.path.isfile(absolute) else result[:80]
                        signature = f"READ:{rel}:{identity}"
                    repeat_count = self._repeat(last_signature, signature, repeat_count)
                    last_signature = signature
                    messages.append({"role": "user", "content": result})
                    continue

                if kind == "SEARCH":
                    query = command["query"]
                    canonical_hash = search_mod._query_hash(query)
                    cached = self._search_cache.get(query, self.task_number)
                    if cached is None and self._search_requests >= self._search_limit:
                        result = f"error: search request limit ({self._search_limit}) reached"
                    else:
                        if cached is None:
                            self._search_requests += 1
                        path, count, error, is_cache = search_mod.execute_search(
                            self.workspace,
                            self.task_number,
                            query,
                            self._search_cache,
                            self._search_provider,
                        )
                        if error:
                            result = f"error: search failed: {error}. Try a different query."
                        elif is_cache:
                            self._cache_hits += 1
                            result = f"Search cache hit. Evidence at {path}. READ that file."
                        else:
                            result = f"Search completed. {count} source(s) saved to {path}. READ that file."
                    if observer.poll_changes():
                        changed = True
                        useful = True
                    signature = f"SEARCH:{canonical_hash}:{result[:80]}"
                    repeat_count = self._repeat(last_signature, signature, repeat_count)
                    last_signature = signature
                    messages.append({"role": "user", "content": result})
                    continue

                if kind == "WRITE":
                    try:
                        result, write_changed, normalized = self._write_command(command["path"], command["content"])
                    except ValueError as exc:
                        result = f"error: {exc}"
                        write_changed = False
                        normalized = command["path"]
                    if observer.poll_changes():
                        changed = True
                    if write_changed:
                        useful = True
                    identity = (
                        _hash_file(os.path.join(self.workspace, normalized))
                        if write_changed else result[:80]
                    )
                    signature = f"WRITE:{normalized}:{identity}"
                    repeat_count = self._repeat(last_signature, signature, repeat_count)
                    last_signature = signature
                    messages.append({"role": "user", "content": result})
                    continue

                if kind == "WRITE_UNTERMINATED":
                    result = f"error: WRITE {command.get('path', '')} must end with END WRITE"
                    signature = f"WRITE_UNTERMINATED:{command.get('path', '')}"
                    repeat_count = self._repeat(last_signature, signature, repeat_count)
                    last_signature = signature
                    messages.append({"role": "user", "content": result})

            if saw_done:
                if observer.task_is_checked():
                    return {"status": "done", "checked": True}
                done_without_check += 1
                if done_without_check >= repeat_limit:
                    return {"status": "stalled", "reason": f"Worker {self.task_number} ended without submitting"}
                messages.append({
                    "role": "user",
                    "content": (
                        f"T{self.task_number} is still unchecked. Write docs/todo.md with only "
                        f"T{self.task_number} changed to [x], then output Done."
                    ),
                })

            if repeat_count >= repeat_limit and not useful and not changed:
                soft_resets += 1
                if soft_resets > self.MAX_SOFT_RESETS:
                    return {"status": "stalled", "reason": "repeated action without progress"}
                manifest = _build_scoped_manifest(self.workspace, self.task_number, output_path, input_paths)
                messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": (
                            self._assignment(task, output_path, input_paths, manifest)
                            + f"\n\n[ERROR: {last_signature} made no progress. Try a different approach.]"
                        ),
                    },
                ]
                observer = Observer(self.workspace, self.task_number)
                last_signature = None
                repeat_count = 0
                continue

            if useful or changed:
                observer.mark_activity()
                done_without_check = 0
            if observer.inactive_for() > inactivity_timeout:
                return {"status": "timeout", "reason": f"inactive {observer.inactive_for():.0f}s"}

        return {"status": "timeout", "reason": f"hard ceiling {hard_ceiling}s"}

    def _write_command(self, path, content):
        safe, err, rel = permissions.check_path_safety(path, self.workspace)
        if not safe or rel in (None, "."):
            raise ValueError(err or "workspace root denied")

        if rel == "docs/todo.md":
            old_text = _read(self.workspace, rel)
            valid, reason = todo_mod.validate_worker_todo_update(old_text, content, self.task_number)
            candidate = content
            if not valid:
                old_tasks = todo_mod.parse_todo(old_text)
                new_tasks = todo_mod.parse_todo(content)
                submitted = any(
                    task["number"] == self.task_number and task["checked"]
                    for task in new_tasks
                )
                old_task = todo_mod.get_task(old_tasks, self.task_number)
                if submitted and old_task and not old_task["checked"]:
                    candidate = todo_mod.set_task_checked(old_text, self.task_number, True)
                else:
                    raise ValueError(f"permission denied: {reason}")
            changed = candidate != old_text
            if changed:
                _write(self.workspace, rel, candidate)
            return (f"wrote {len(candidate)} bytes to {rel}" if changed else f"unchanged: {rel}", changed, rel)

        allowed, reason = permissions.check_write_permission(rel, permissions.ROLE_WORKER, self.task_number)
        if not allowed:
            raise ValueError(f"permission denied: {reason}")
        absolute = os.path.join(self.workspace, rel)
        existing = None
        if os.path.isfile(absolute):
            with open(absolute, encoding="utf-8") as handle:
                existing = handle.read()
        changed = existing != content
        if changed:
            _write(self.workspace, rel, content)
        return (f"wrote {len(content)} bytes to {rel}" if changed else f"unchanged: {rel}", changed, rel)


def _safe_artifact_path(workspace, output_path):
    safe, err, rel = permissions.check_path_safety(output_path, workspace)
    if not safe or rel in (None, "."):
        raise ValueError(f"unsafe artifact path: {err or 'workspace root'}")
    return os.path.join(workspace, rel)


def _collect_artifact_summaries(workspace, tasks):
    summaries = []
    for task in tasks:
        output, _ = todo_mod.get_task_metadata(tasks, task["number"])
        try:
            path = _safe_artifact_path(workspace, output)
        except ValueError:
            continue
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                body = handle.read()
        except OSError:
            continue
        preview = re.sub(r"\s+", " ", body[:500]).strip()
        summaries.append(f"- T{task['number']} — {task['description']} ({output}, {len(body)}b): {preview}")
    return "\n".join(summaries) if summaries else "(no artifacts)"


class ArtifactReviewAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config, task_number):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number

    def run(self, backend):
        tasks = todo_mod.parse_todo(_read(self.workspace, "docs/todo.md"))
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return self._make_result("ERROR", "task not found")
        output_path, _ = todo_mod.get_task_metadata(tasks, self.task_number)
        try:
            artifact_path = _safe_artifact_path(self.workspace, output_path)
        except ValueError as exc:
            return self._make_result("REWORK", str(exc))
        if not os.path.exists(artifact_path):
            return self._make_result("REWORK", "artifact file does not exist")
        if not os.path.isfile(artifact_path):
            return self._make_result("REWORK", "artifact path is not a file")
        try:
            with open(artifact_path, encoding="utf-8") as handle:
                artifact = handle.read()
        except OSError as exc:
            return self._make_result("REWORK", f"cannot read artifact: {exc}")
        if not artifact.strip():
            return self._make_result("REWORK", "artifact is empty")

        research_dir = search_mod._research_dir(self.workspace, self.task_number)
        research_files = []
        if os.path.isdir(research_dir):
            research_files = [
                name for name in sorted(os.listdir(research_dir))
                if re.fullmatch(r"search-\d+\.md", name)
                and os.path.isfile(os.path.join(research_dir, name))
            ]
        if research_files and not search_mod.has_citations(artifact, research_dir):
            return self._make_result("REWORK", "artifact does not cite supporting research evidence")

        research_parts = []
        budget = 8000
        for name in research_files:
            if budget <= 0:
                break
            try:
                with open(os.path.join(research_dir, name), encoding="utf-8") as handle:
                    content = handle.read(min(2000, budget))
            except OSError:
                continue
            research_parts.append(f"### {name}\n{content}")
            budget -= len(content)
        research = "\n\n## Supporting research\n" + "\n\n".join(research_parts) if research_parts else ""
        prompt = (
            "# Review Assignment\n\n"
            f"Task:\n{task['description']}\n\n"
            f"Required output:\n{output_path}\n\n"
            f"Artifact:\n{artifact}\n{research}\n\n"
            "Judge only whether this artifact materially fulfills this task.\n"
            "Return ACCEPT or REWORK on the first line, followed by a nonempty Reason:."
        )
        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {"role": "user", "content": prompt},
        ]
        for _ in range(self.RETRY_LIMIT):
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return self._make_result("ERROR", f"model request failed: {exc}")
            raw = (response.get("content") or "").strip()
            result = self._parse(_clean_fences(raw))
            if result:
                result["task_number"] = self.task_number
                self._save_review(result)
                return result
            messages.extend([
                {"role": "assistant", "content": raw or "[no output]"},
                {"role": "user", "content": "Return ACCEPT or REWORK with a nonempty Reason:."},
            ])
        return self._make_result("ERROR", "failed to produce valid review after retries")

    @staticmethod
    def _parse(content):
        lines = content.strip().splitlines()
        if not lines or lines[0].strip() not in ("ACCEPT", "REWORK"):
            return None
        match = re.search(r"^Reason:\s*(.+)$", content, re.MULTILINE | re.DOTALL)
        if not match or not match.group(1).strip():
            return None
        return {"verdict": lines[0].strip(), "reason": match.group(1).strip()}

    def _make_result(self, verdict, reason):
        result = {"verdict": verdict, "reason": reason, "task_number": self.task_number}
        self._save_review(result)
        return result

    def _save_review(self, result):
        try:
            tasks = todo_mod.parse_todo(_read(self.workspace, "docs/todo.md"))
            output, _ = todo_mod.get_task_metadata(tasks, self.task_number)
            artifact_hash = _hash_file(_safe_artifact_path(self.workspace, output))
        except (ValueError, OSError):
            artifact_hash = "?"
        content = (
            f"# Review T{self.task_number}\n\n"
            f"Verdict: {result['verdict']}\n"
            f"Reason: {result.get('reason', '')}\n"
            f"Task: {self.task_number}\n"
            f"Artifact hash: {artifact_hash}\n"
        )
        _write(self.workspace, f"docs/reviews/T{self.task_number}.md", content)


class CompletionReviewAdapter:
    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        tasks = todo_mod.parse_todo(_read(self.workspace, "docs/todo.md"))
        prompt = (
            "# Completion Review\n\n"
            f"Original request:\n{_read(self.workspace, 'docs/task.md')}\n\n"
            f"Completed tasks:\n{_read(self.workspace, 'docs/todo.md')}\n\n"
            f"Artifact summaries:\n{_collect_artifact_summaries(self.workspace, tasks)}\n\n"
            "Does the accepted work fully satisfy the original request?\n"
            "Return COMPLETE on the first line, or MISSING followed by one or more bullet items."
        )
        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {"role": "user", "content": prompt},
        ]
        for _ in range(self.RETRY_LIMIT):
            try:
                response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            except Exception as exc:
                return {"verdict": "ERROR", "reason": f"model request failed: {exc}"}
            raw = (response.get("content") or "").strip()
            result = self._parse(_clean_fences(raw))
            if result:
                return result
            messages.extend([
                {"role": "assistant", "content": raw or "[no output]"},
                {"role": "user", "content": "Return COMPLETE or MISSING with nonempty bullet items."},
            ])
        return {"verdict": "ERROR", "reason": "failed to produce valid completion review"}

    @staticmethod
    def _parse(content):
        lines = content.strip().splitlines()
        if not lines:
            return None
        first = lines[0].strip()
        if first == "COMPLETE":
            return {"verdict": "COMPLETE", "missing": []}
        if first != "MISSING":
            return None
        missing = []
        seen = set()
        for line in lines[1:]:
            match = re.match(r"^\s*[-*]\s+(.+)$", line)
            if not match:
                continue
            item = match.group(1).strip()
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                missing.append(item)
        return {"verdict": "MISSING", "missing": missing} if missing else None
