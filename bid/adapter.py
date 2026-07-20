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
            commands.append({"type": "WRITE", "path": path, "content": "\n".join(body_lines)})
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
                    "Commands:\n"
                    "  READ <path>  — read a file\n"
                    "  WRITE <path> — write content, ended by END WRITE on its own line\n"
                    "  Done         — finish (after checking your task checkbox)\n\n"
                    "Example: write your artifact then submit:\n"
                    "WRITE docs/work/T1.md\n"
                    "Your artifact content here.\n"
                    "END WRITE\n"
                    "WRITE docs/todo.md\n"
                    "- [x] T1 — Task description\n"
                    "END WRITE\n"
                    "Done\n\n"
                    "Read inputs first. Write your artifact. "
                    "To submit, write docs/todo.md with your checkbox set to [x]. "
                    "Then output Done."
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

        while observer.elapsed() < hard_ceiling:
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
                return f"permission denied: {reason}"
            _write(self.workspace, rel, content)
            return f"wrote {len(content)} bytes to {rel}"

        allowed, err_msg = permissions.check_write_permission(rel, permissions.ROLE_WORKER, self.task_number)
        if not allowed:
            return f"permission denied: {err_msg}"

        _write(self.workspace, rel, content)
        return f"wrote {len(content)} bytes to {rel}"
