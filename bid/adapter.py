import os
import re
import time

from . import permissions
from . import todo as todo_mod
from .observer import Observer


# ── Helpers ──────────────────────────────────────────────────────────

def _read(workspace, rel):
    path = os.path.join(workspace, rel)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(workspace, rel, content):
    path = os.path.join(workspace, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _clean_fences(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    return text


# ── Command parsing ──────────────────────────────────────────────────

_COMMAND_RE = re.compile(
    r"^(?P<cmd>READ|WRITE|Done)\b\s*(?P<arg>.*)",
    re.MULTILINE,
)


def _parse_content_into_turns(content):
    """Return a list of commands, each a dict with type/path/content/is_done.

    WRITE content spans from the WRITE line to the next END WRITE or end of content.
    """
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

        if stripped.startswith("READ "):
            path = stripped[5:].strip()
            commands.append({"type": "READ", "path": path})
            i += 1
            continue

        if stripped.startswith("WRITE "):
            path = stripped[6:].strip()
            i += 1
            body_lines = []
            while i < len(lines):
                if lines[i].strip() == "END WRITE":
                    i += 1
                    break
                body_lines.append(lines[i])
                i += 1
            body = "\n".join(body_lines)
            # Model sometimes serializes newlines as literal \n
            body = body.replace("\\n", "\n")
            commands.append({"type": "WRITE", "path": path, "content": body})
            continue

        i += 1

    return commands


def _build_scoped_manifest(workspace, task_number, output_path, input_paths):
    """Return a minimal file listing for a Worker."""
    lines = ["# Available files", ""]
    todo_path = os.path.join(workspace, "docs/todo.md")
    if os.path.exists(todo_path):
        lines.append("- docs/todo.md")
    worker_path = os.path.join(workspace, "docs/worker.md")
    if os.path.exists(worker_path):
        lines.append("- docs/worker.md")
    for p in input_paths:
        abs_p = os.path.join(workspace, p)
        if os.path.exists(abs_p):
            lines.append(f"- {p}")
    if output_path:
        out_parent = os.path.dirname(output_path)
        if out_parent:
            lines.append(f"- {out_parent}/ (output directory)")
    return "\n".join(lines) + "\n"


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
            response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
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
        return len(tasks) >= 1


class WorkerAdapter:
    """Worker reads inputs with READ, writes artifact with WRITE, submits checkbox, outputs Done.

    BID owns the loop; the model issues only text commands.
    """

    MAX_SOFT_RESETS = 3

    def __init__(self, config, task_number):
        self.config = config
        self.workspace = config["workspace"]
        self.task_number = task_number

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
                    "  WRITE <path>    — write content; end with END WRITE on its own line\n"
                    "  Done            — finish (after checking T1 in docs/todo.md)\n\n"
                    "Example:\n"
                    f"WRITE {output_path}\n"
                    "Your artifact content here.\n"
                    "END WRITE\n"
                    f"WRITE docs/todo.md\n"
                    f"- [x] T{self.task_number} — {task['description']}\n"
                    "END WRITE\n"
                    "Done\n\n"
                    "First READ docs/todo.md to see the current task list. "
                    "Preserve all existing task lines exactly, changing only T{self.task_number} from [ ] to [x]. "
                    "Then write your artifact. "
                    "Finally output Done."
                ),
            },
        ]

        observer = Observer(self.workspace, self.task_number)
        hard_ceiling = self.config.get("worker_timeout", 3600)
        inactivity_timeout = self.config.get("inactivity_timeout", 600)
        repeat_limit = self.config.get("repeat_action_limit", 5)
        soft_resets = 0
        done_without_check = 0
        last_sig = None
        turn_repeat = 0
        total_turns = 0

        while observer.elapsed() < hard_ceiling:
            total_turns += 1
            if total_turns > 20:
                return {"status": "stalled", "reason": "too many turns without completion"}
            response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
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
                                useful = True
                        sig = f"READ {cmd['path']}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        messages.append({"role": "user", "content": result})
                        continue

                    if cmd["type"] == "WRITE":
                        result = self._write_command(cmd["path"], cmd["content"])
                        if not result.startswith("error"):
                            useful = True
                        if observer.poll_changes():
                            changed = True
                        sig = f"WRITE {cmd['path']}|{result[:50]}"
                        if sig == last_sig:
                            turn_repeat += 1
                        else:
                            turn_repeat = 0
                        last_sig = sig
                        messages.append({"role": "user", "content": result})
                        continue

            # ── Done processing ──────────────────────────────────────
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

            # ── Soft reset on repeat stall ───────────────────────────
            if turn_repeat >= repeat_limit and not changed and not useful:
                soft_resets += 1
                if soft_resets > self.MAX_SOFT_RESETS:
                    return {"status": "stalled", "reason": f"repeated action without progress"}
                system = messages[0]
                manifest = _build_scoped_manifest(self.workspace, self.task_number, output_path, input_paths)
                assignment = messages[1]["content"]
                if "\n\n[ERROR]" in assignment:
                    base = assignment[: assignment.index("\n\n[ERROR]")]
                else:
                    base = assignment.split("Commands:")[0] if "Commands:" in assignment else assignment
                messages = [
                    system,
                    {
                        "role": "user",
                        "content": (
                            f"{manifest}\n{base}\n\n"
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

            # ── Activity marking ─────────────────────────────────────
            if useful or changed:
                observer.mark_activity()
                done_without_check = 0

            if observer.inactive_for() > inactivity_timeout:
                return {"status": "timeout", "reason": f"inactive {observer.inactive_for():.0f}s"}

        return {"status": "timeout", "reason": f"hard ceiling {hard_ceiling}s"}

    def _write_command(self, path, content):
        safe, err, rel = permissions.check_path_safety(path, self.workspace)
        if not safe:
            return err

        if rel == "docs/todo.md":
            old_text = _read(self.workspace, "docs/todo.md")
            valid, reason = todo_mod.validate_worker_todo_update(old_text, content, self.task_number)
            if not valid:
                # Fallback: merge checkbox into existing TODO
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
                return f"permission denied: {reason}"
            _write(self.workspace, rel, content)
            return f"wrote {len(content)} bytes to {rel}"

        allowed, err_msg = permissions.check_write_permission(rel, permissions.ROLE_WORKER, self.task_number)
        if not allowed:
            return f"permission denied: {err_msg}"

        _write(self.workspace, rel, content)
        return f"wrote {len(content)} bytes to {rel}"


# ── Helpers for ManagerReviewAdapter ──────────────────────────────────

_DEFAULT_OUTPUT_DIR = "docs/work"


def _find_last_task_line(todo_text):
    """Return the index of the last line matching a task marker."""
    lines = todo_text.split("\n")
    for i in range(len(lines) - 1, -1, -1):
        if re.match(r"^\s*[-*]\s+\[[ x]\]\s+T\d+\b", lines[i]):
            return i
    return -1


def _parse_verdict(content):
    """Parse a review verdict from model output.

    Returns {verdict: "DONE"|"REWORK"|None, reopen: [int], add: [str]}.
    """
    result = {"verdict": None, "reopen": [], "add": []}
    v = re.search(r"# Verdict\s*\n\s*(DONE|REWORK)", content)
    if v:
        result["verdict"] = v.group(1)
    reopen = re.search(r"# Reopen\s*(.*?)(?=#\s|\Z)", content, re.DOTALL)
    if reopen:
        for line in reopen.group(1).split("\n"):
            m = re.match(r"^\s*[-*]\s+T(\d+)", line)
            if m:
                result["reopen"].append(int(m.group(1)))
    add = re.search(r"# Add\s*(.*?)(?=#\s|\Z)", content, re.DOTALL)
    if add:
        for line in add.group(1).split("\n"):
            m = re.match(r"^\s*[-*]\s+(.*)", line)
            if m and not m.group(1).startswith("T") and m.group(1).strip():
                result["add"].append(m.group(1).strip())
    return result


def _apply_rework(workspace, todo_text, verdict):
    """Uncheck reopened tasks and append new tasks.  Return True if TO DO changed."""
    tasks = todo_mod.parse_todo(todo_text)
    changed = False
    for tnum in verdict["reopen"]:
        t = todo_mod.get_task(tasks, tnum)
        if t and t["checked"]:
            todo_text = todo_mod.set_task_checked(todo_text, tnum, False)
            changed = True
    max_num = max((t["number"] for t in tasks), default=0)
    last_line = _find_last_task_line(todo_text)
    for desc in verdict["add"]:
        max_num += 1
        line = f"\n- [ ] T{max_num} — {desc}"
        if last_line >= 0:
            lines = todo_text.split("\n")
            lines.insert(last_line + 1, line.strip())
            todo_text = "\n".join(lines)
        else:
            todo_text += line
        last_line += 1
        changed = True
    if changed:
        _write(workspace, "docs/todo.md", todo_text)
    return changed


def _collect_artifact_summaries(workspace, tasks):
    """Return a compact Markdown summary of all task artifacts.

    Uses declared output path or defaults to docs/work/T{N}.md.
    """
    lines = ["## Artifacts", ""]
    for t in tasks:
        out, _ = todo_mod.get_task_metadata(tasks, t["number"])
        path = os.path.join(workspace, out)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                body = f.read()
            preview = body[:200].replace("\n", " ")
            lines.append(f"### T{t['number']} — {out} ({len(body)}b)")
            lines.append(f"{preview}")
            lines.append("")
    return "\n".join(lines) if len(lines) > 2 else ""


class ManagerReviewAdapter:
    """Manager reviews artifacts and returns a plain-text verdict.

    Verdict DONE → BID writes project-status.md.
    Verdict REWORK → BID unchecks tasks and appends new tasks.
    """

    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        task_md = _read(self.workspace, "docs/task.md")
        todo_text = _read(self.workspace, "docs/todo.md")
        tasks = todo_mod.parse_todo(todo_text)
        decisions = _read(self.workspace, "docs/decisions.md")
        artifacts = _collect_artifact_summaries(self.workspace, tasks)

        prompt = self._build_prompt(task_md, todo_text, artifacts, decisions)
        messages = [
            {"role": "system", "content": _read(self.workspace, "docs/manager.md")},
            {"role": "user", "content": prompt},
        ]

        for attempt in range(self.RETRY_LIMIT):
            response = backend.run(messages, [], max_tokens=self.config.get("max_tokens", 8192))
            content = (response.get("content") or "").strip()
            content = _clean_fences(content)

            verdict = _parse_verdict(content)
            if verdict["verdict"] == "DONE":
                if not todo_mod.all_checked(todo_mod.parse_todo(_read(self.workspace, "docs/todo.md"))):
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": "Not all tasks are checked. Review again."})
                    continue
                _write(self.workspace, "docs/project-status.md", "# Project Status\n\nDONE\n")
                return {"status": "done"}

            if verdict["verdict"] == "REWORK":
                changed = _apply_rework(self.workspace, _read(self.workspace, "docs/todo.md"), verdict)
                if changed:
                    reopened = verdict["reopen"]
                    added = verdict["add"]
                    return {"status": "rework", "reopened": reopened, "added": added}
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": "No tasks were reopened or added. Specify which tasks need rework or which to add."})
                continue

            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": "Return # Verdict with DONE or REWORK, optionally # Reopen and # Add sections."
            })

        return {"status": "error", "reason": "failed to produce valid review after 3 attempts"}

    def _build_prompt(self, task_md, todo_text, artifacts, decisions):
        parts = [
            f"# Original Task\n\n{task_md}\n",
            f"# Current TODO\n\n{todo_text}\n",
        ]
        if artifacts:
            parts.append(artifacts)
        if decisions.strip():
            parts.append(f"# Decision Log\n\n{decisions}\n")
        parts.append(
            "# Instructions\n\n"
            "Review the completed work. Return only:\n\n"
            "```\n"
            "# Verdict\n"
            "\n"
            "DONE\n"
            "\n"
            "# Reason\n"
            "All tasks complete.\n"
            "```\n\n"
            "Or if rework is needed:\n\n"
            "```\n"
            "# Verdict\n"
            "\n"
            "REWORK\n"
            "\n"
            "# Reopen\n"
            "\n"
            "- T3 — Reason the artifact is inadequate.\n"
            "\n"
            "# Add\n"
            "\n"
            "- New task description.\n"
            "```\n\n"
            "Existing task lines must be preserved verbatim when reopening."
        )
        return "\n".join(parts)
