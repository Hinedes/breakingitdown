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
                commands.append({"type": "WRITE_UNTERMINATED"})
                continue
            commands.append({"type": "WRITE", "path": path, "content": "\n".join(body_lines)})
            continue

        i += 1

    return commands


def _build_scoped_manifest(workspace, task_number, output_path, input_paths):
    lines = ["# Available files", ""]
    for rel in ["docs/todo.md", "docs/worker.md"]:
        try:
            p = _safe_path(workspace, rel)
            if os.path.exists(p):
                lines.append(f"- {rel}")
        except ValueError:
            pass
    for p in input_paths:
        try:
            ap = _safe_path(workspace, p)
            if os.path.exists(ap):
                lines.append(f"- {p}")
        except ValueError:
            pass
    if output_path:
        out_parent = os.path.dirname(output_path)
        if out_parent:
            lines.append(f"- {out_parent}/ (output directory)")
    research = search_mod._research_dir(workspace, task_number)
    if os.path.isdir(research):
        lines.append(f"- docs/research/T{task_number}/")
    return "\n".join(lines) + "\n"


# ── TODO validation ──────────────────────────────────────────────────

_TASK_LINE_CHECK = re.compile(r"^\s*[-*]\s+\[([ x])\]\s+(T\d+)\b\s*(.*)")


def validate_todo_tasks(tasks):
    if not tasks:
        return False, "no tasks"
    numbers = [t["number"] for t in tasks]
    if numbers != list(range(1, len(tasks) + 1)):
        return False, f"task numbers must be sequential T1..T{len(tasks)}, got {numbers}"
    seen_outputs = set()
    for t in tasks:
        if t["id"] != f"T{t['number']}":
            return False, f"{t['id']} has noncanonical format (expected T{t['number']})"
        if t["checked"]:
            return False, f"{t['id']} must start unchecked"
        if not t["description"].strip():
            return False, f"{t['id']} has empty description"
        out, inputs = todo_mod.get_task_metadata(tasks, t["number"])
        for p in [out] + inputs:
            if not p:
                return False, f"{t['id']} has empty path"
            safe, err, rel = permissions.check_path_safety(p, "/dummy")
            if not safe:
                return False, f"{t['id']} has unsafe path: {err}"
            if rel == "." or not rel:
                return False, f"{t['id']} path is workspace root"
            if _is_reserved_control_path(rel):
                return False, f"{t['id']} path targets a protected file: {rel}"
        if out:
            _, _, norm_out = permissions.check_path_safety(out, "/dummy")
            if norm_out in seen_outputs:
                return False, f"duplicate output path: {out}"
            seen_outputs.add(norm_out)
    return True, None


def _is_reserved_control_path(rel):
    parts = rel.split("/")
    if ".bid" in parts:
        return True
    if "docs/reviews" in rel or rel.startswith("docs/reviews/"):
        return True
    if rel in ("docs/task.md", "docs/todo.md", "docs/project-status.md", "docs/decisions.md",
               "docs/manager.md", "docs/worker.md"):
        return True
    return False


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
                    "Create a numbered checklist for this task. "
                    "Return only the Markdown checklist lines, one per task, like:\n"
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

    def __init__(self, config, task_number, search_provider=None):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number
        self._search_provider = search_provider or search_mod.MockSearchProvider()
        self._search_cache = search_mod.SearchCache()
        self._search_count = 0
        self._search_limit = config.get("max_searches_per_worker", 10)

    def run(self, backend):
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return {"status": "error", "reason": f"T{self.task_number} not found in TODO"}
        output_path, input_paths = todo_mod.get_task_metadata(tasks, self.task_number)

        worker_policy = _read(self.workspace, "docs/worker.md")
        manifest = _build_scoped_manifest(self.workspace, self.task_number, output_path, input_paths)

        messages = [
            {"role": "system", "content": f"/no_think\n{worker_policy}"},
            {
                "role": "user",
                "content": (
                    f"{manifest}"
                    f"\nTask T{self.task_number}: {task['description']}\n"
                    f"Output file: {output_path}\n"
                    f"{'Input files: ' + ', '.join(input_paths) if input_paths else ''}\n\n"
                    "Only use these commands:\n"
                    "  READ <path>     — read a file\n"
                    "  SEARCH <query>  — search for current information\n"
                    "  WRITE <path>    — write content; end with END WRITE on its own line\n"
                    f"  Done            — finish (after checking T{self.task_number} in docs/todo.md)\n\n"
                    "Example:\n"
                    f"WRITE {output_path}\n"
                    "Your artifact content here.\n"
                    "END WRITE\n"
                    f"WRITE docs/todo.md\n"
                    f"- [x] T{self.task_number} — {task['description']}\n"
                    "END WRITE\n"
                    "Done\n\n"
                    "First READ docs/todo.md to see the current task list. "
                    f"Preserve all existing task lines exactly, changing only T{self.task_number} from [ ] to [x]. "
                    "Then write your artifact. "
                    "Finally output Done."
                ),
            },
        ]

        _task_assignment = (
            f"\nTask T{self.task_number}: {task['description']}\n"
            f"Output file: {output_path}\n"
            f"{'Input files: ' + ', '.join(input_paths) if input_paths else ''}\n\n"
            "Only use these commands:\n"
            "  READ <path>     — read a file\n"
            "  WRITE <path>    — write content; end with END WRITE on its own line\n"
            f"  Done            — finish (after checking T{self.task_number} in docs/todo.md)\n\n"
            "Example:\n"
            f"WRITE {output_path}\n"
            "Your artifact content here.\n"
            "END WRITE\n"
            f"WRITE docs/todo.md\n"
            f"- [x] T{self.task_number} — {task['description']}\n"
            "END WRITE\n"
            "Done\n\n"
            "First READ docs/todo.md to see the current task list. "
            f"Preserve all existing task lines exactly, changing only T{self.task_number} from [ ] to [x]. "
            "Then write your artifact. "
            "Finally output Done."
        )

        observer = Observer(self.workspace, self.task_number)
        hard_ceiling = self.config.get("worker_timeout", 3600)
        inactivity_timeout = self.config.get("inactivity_timeout", 600)
        repeat_limit = self.config.get("repeat_action_limit", 5)
        session_start = time.monotonic()
        soft_resets = 0
        done_without_check = 0
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
                    "content": "Use READ <path>, WRITE <path>\\n<content>\\nEND WRITE, or Done."
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
                            self._search_count += 1
                            path, n, err = search_mod.execute_search(
                                self.workspace, self.task_number, cmd["query"],
                                self._search_cache, self._search_provider,
                            )
                            if err:
                                result = f"error: search failed: {err}"
                            else:
                                result = f"Search completed. {n} source(s) saved to {os.path.relpath(path, self.workspace)}. READ that file to continue."
                                useful = True
                        sig = f"SEARCH {cmd['query']}|{result[:50]}"
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
                        result = "error: WRITE must end with END WRITE on its own line"
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
                if observer.task_is_checked():
                    return {"status": "done", "checked": True}
                done_without_check += 1
                if done_without_check >= repeat_limit:
                    return {
                        "status": "stalled",
                        "reason": f"Worker {self.task_number} ended without submitting",
                    }
                messages.append({
                    "role": "user",
                    "content": (
                        f"T{self.task_number} is still unchecked. "
                        "Submit by writing docs/todo.md with T{self.task_number} set to [x]. "
                        "Then output Done."
                    ),
                })
                changed = True

            # Soft reset on repeat stall
            if turn_repeat >= repeat_limit and not changed and not useful:
                soft_resets += 1
                if soft_resets > self.MAX_SOFT_RESETS:
                    return {"status": "stalled", "reason": f"repeated action without progress"}
                system = messages[0]
                manifest = _build_scoped_manifest(self.workspace, self.task_number, output_path, input_paths)
                messages = [
                    system,
                    {
                        "role": "user",
                        "content": (
                            f"{manifest}{_task_assignment}\n\n"
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
                done_without_check = 0

            if observer.inactive_for() > inactivity_timeout:
                return {"status": "timeout", "reason": f"inactive {observer.inactive_for():.0f}s"}

        return {"status": "timeout", "reason": f"hard ceiling {hard_ceiling}s"}

    def _write_command(self, path, content):
        safe, err, rel = permissions.check_path_safety(path, self.workspace)
        if not safe:
            raise ValueError(err)

        if rel == "docs/todo.md":
            old_text = _read(self.workspace, "docs/todo.md")
            valid, reason = todo_mod.validate_worker_todo_update(old_text, content, self.task_number)
            if not valid:
                old_tasks = todo_mod.parse_todo(old_text)
                new_tasks = todo_mod.parse_todo(content)
                merged = old_text
                toggled = False
                for nt in new_tasks:
                    if nt["number"] == self.task_number and nt["checked"]:
                        ot = todo_mod.get_task(old_tasks, self.task_number)
                        if ot and not ot["checked"]:
                            merged = todo_mod.set_task_checked(merged, self.task_number, True)
                            toggled = True
                            break
                if toggled:
                    _write(self.workspace, rel, merged)
                    return f"wrote {len(merged)} bytes to {rel}"
                raise ValueError(f"permission denied: {reason}")
            _write(self.workspace, rel, content)
            return f"wrote {len(content)} bytes to {rel}"

        allowed, err_msg = permissions.check_write_permission(rel, permissions.ROLE_WORKER, self.task_number)
        if not allowed:
            raise ValueError(f"permission denied: {err_msg}")

        _write(self.workspace, rel, content)
        return f"wrote {len(content)} bytes to {rel}"


# ── Helpers for review adapters ──────────────────────────────────────

def _find_last_task_line(todo_text):
    lines = todo_text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^\s*[-*]\s+\[[ x]\]\s+T\d+\b", lines[i]):
            return i
    return -1


def _safe_artifact_path(workspace, output_path):
    safe, err, rel = permissions.check_path_safety(output_path, workspace)
    if not safe:
        raise ValueError(f"unsafe artifact path: {err}")
    return os.path.join(workspace, rel)


def _collect_artifact_summaries(workspace, tasks):
    lines = ["## Artifacts", ""]
    for t in tasks:
        out, _ = todo_mod.get_task_metadata(tasks, t["number"])
        try:
            path = _safe_artifact_path(workspace, out)
        except ValueError:
            continue
        if os.path.exists(path) and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    body = f.read()
                preview = body[:150].replace("\n", " ")
                lines.append(f"### T{t['number']} — {out} ({len(body)}b)")
                lines.append(f"{preview}")
                lines.append("")
            except OSError:
                pass
    return "\n".join(lines) if len(lines) > 2 else ""


# ── ArtifactReviewAdapter ────────────────────────────────────────────

class ArtifactReviewAdapter:
    """Judge one complete artifact against its one task."""

    RETRY_LIMIT = 3

    def __init__(self, config, task_number):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number

    def run(self, backend):
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        task = todo_mod.get_task(tasks, self.task_number)
        if not task:
            return self._make_result("ERROR", "task not found")

        output_path, _ = todo_mod.get_task_metadata(tasks, self.task_number)
        try:
            artifact_path = _safe_artifact_path(self.workspace, output_path)
        except ValueError as e:
            return self._make_result("REWORK", str(e))

        if not os.path.exists(artifact_path):
            return self._make_result("REWORK", "artifact file does not exist")
        if not os.path.isfile(artifact_path):
            return self._make_result("REWORK", "artifact path is not a file")

        try:
            with open(artifact_path, encoding="utf-8") as f:
                artifact_content = f.read()
        except OSError as e:
            return self._make_result("REWORK", f"cannot read artifact: {e}")

        if not artifact_content.strip():
            return self._make_result("REWORK", "artifact is empty")

        prompt = (
            "# Review Assignment\n\n"
            f"Task:\n{task['description']}\n\n"
            f"Required output:\n{output_path}\n\n"
            f"Artifact:\n{artifact_content}\n\n"
            "Judge only whether this artifact materially fulfills its task.\n\n"
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
                return self._make_result("ERROR", f"model request failed: {exc}")

            content = (response.get("content") or "").strip()
            raw = content
            content = _clean_fences(content)

            result = self._parse(content)
            if result:
                result["task_number"] = self.task_number
                self._save_review(result)
                return result

            messages.append({"role": "assistant", "content": raw or "[no output]"})
            messages.append({"role": "user", "content": "Return ACCEPT or REWORK with Reason."})

        return self._make_result("ERROR", "failed to produce valid review after retries")

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

    def _make_result(self, verdict, reason):
        result = {"verdict": verdict, "reason": reason, "task_number": self.task_number}
        self._save_review(result)
        return result

    def _save_review(self, result):
        path = os.path.join(self.workspace, "docs/reviews", f"T{self.task_number}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            out, _ = todo_mod.get_task_metadata(
                todo_mod.parse_todo(_read(self.workspace, "docs/todo.md")),
                self.task_number,
            )
            ap = _safe_artifact_path(self.workspace, out)
            h = _hash_file(ap)
        except (ValueError, OSError):
            h = "?"
        content = (
            f"# Review T{self.task_number}\n\n"
            f"Verdict: {result['verdict']}\n"
            f"Reason: {result.get('reason', '')}\n"
            f"Task: {self.task_number}\n"
            f"Artifact hash: {h}\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# ── CompletionReviewAdapter ──────────────────────────────────────────

class CompletionReviewAdapter:
    """After individual artifacts are accepted, check whether the original request is covered."""

    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        task_md = _read(self.workspace, "docs/task.md")
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)

        summaries = []
        for t in tasks:
            out, _ = todo_mod.get_task_metadata(tasks, t["number"])
            try:
                path = _safe_artifact_path(self.workspace, out)
            except ValueError:
                continue
            if os.path.exists(path) and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        body = f.read()
                    summaries.append(f"- T{t['number']} ({out}, {len(body)}b)")
                except OSError:
                    pass

        summary_text = "\n".join(summaries) if summaries else "(no artifacts)"

        prompt = (
            "# Completion Review\n\n"
            f"Original request:\n{task_md}\n\n"
            f"Completed tasks:\n{todo_text}\n\n"
            f"Artifacts:\n{summary_text}\n\n"
            "Does the completed work fully satisfy the original request?\n\n"
            "Return exactly one of:\n\n"
            "COMPLETE\n"
            "Reason: ...\n\n"
            "MISSING\n"
            "- description of missing deliverable\n"
            "- description of missing deliverable"
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
            messages.append({"role": "user", "content": "Return COMPLETE or MISSING with details."})

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
                    if item and item.lower() not in seen:
                        seen.add(item.lower())
                        missing.append(item)
            if not missing:
                return None
            return {"verdict": "MISSING", "missing": missing}
        return None


# ── Legacy helpers (kept for backward compat, not used by scheduler) ──

def _find_last_task_line(todo_text):
    lines = todo_text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^\s*[-*]\s+\[[ x]\]\s+T\d+\b", lines[i]):
            return i
    return -1


def _collect_artifact_summaries(workspace, tasks):
    lines = ["## Artifacts", ""]
    for t in tasks:
        out, _ = todo_mod.get_task_metadata(tasks, t["number"])
        try:
            path = _safe_artifact_path(workspace, out)
        except ValueError:
            continue
        if os.path.exists(path) and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    body = f.read()
                preview = body[:150].replace("\n", " ")
                lines.append(f"### T{t['number']} — {out} ({len(body)}b)")
                lines.append(f"{preview}")
                lines.append("")
            except OSError:
                pass
    return "\n".join(lines) if len(lines) > 2 else ""
