import hashlib
import json
import os
import time

from . import todo as todo_mod


class Observer:
    def __init__(self, workspace, task_number):
        self.workspace = workspace
        self.task_number = task_number
        self._snapshot = self._tree_state()
        self.task_was_checked = self.task_is_checked()
        self.last_activity = time.time()
        self.start_time = time.time()
        self._last_action = None
        self._repeat_count = 0

    def _tree_state(self):
        state = {}
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [directory for directory in dirs if directory != ".bid"]
            for filename in files:
                path = os.path.join(root, filename)
                try:
                    with open(path, "rb") as file:
                        digest = hashlib.sha256(file.read()).hexdigest()
                except OSError:
                    continue
                relative = os.path.relpath(path, self.workspace).replace(os.sep, "/")
                state[relative] = digest
        return state

    def snapshot_tree(self):
        self._snapshot = self._tree_state()

    def poll_changes(self):
        current = self._tree_state()
        changed = sorted({
            path
            for path in set(self._snapshot) | set(current)
            if self._snapshot.get(path) != current.get(path)
        })
        self._snapshot = current
        if changed:
            self.mark_activity()
        return changed

    def changed_files(self):
        current = self._tree_state()
        return sorted({
            path
            for path in set(self._snapshot) | set(current)
            if self._snapshot.get(path) != current.get(path)
        })

    def task_is_checked(self):
        if not self.task_number:
            return False
        todo_path = os.path.join(self.workspace, "docs/todo.md")
        if not os.path.exists(todo_path):
            return False
        with open(todo_path, encoding="utf-8") as file:
            tasks = todo_mod.parse_todo(file.read())
        task = todo_mod.get_task(tasks, self.task_number)
        return bool(task and task["checked"])

    def task_just_became_checked(self):
        now = self.task_is_checked()
        changed = now and not self.task_was_checked
        self.task_was_checked = now
        return changed

    def seen_done(self, text):
        if not text:
            return False
        lines = text.strip().split("\n")
        for line in reversed(lines):
            stripped = line.strip()
            if stripped == "Done":
                return True
            if stripped and not stripped.startswith("```"):
                return False
        return False

    def record_action(self, name, arguments, result):
        signature = json.dumps(
            {"name": name, "arguments": arguments, "result": result},
            sort_keys=True,
            ensure_ascii=False,
        )
        if signature == self._last_action:
            self._repeat_count += 1
        else:
            self._last_action = signature
            self._repeat_count = 1
        return self._repeat_count

    def reset_action_loop(self):
        self._last_action = None
        self._repeat_count = 0

    def mark_activity(self):
        self.last_activity = time.time()

    def elapsed(self):
        return time.time() - self.start_time

    def inactive_for(self):
        return time.time() - self.last_activity
