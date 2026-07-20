import os
import time
from . import todo as todo_mod


class Observer:
    def __init__(self, workspace, task_number):
        self.workspace = workspace
        self.task_number = task_number
        self.task_was_checked = False
        self._snapshot = {}
        self.last_activity = time.time()
        self.start_time = time.time()

    def snapshot_tree(self):
        self._snapshot = {}
        for root, dirs, files in os.walk(self.workspace):
            if ".bid" in root:
                continue
            for f in files:
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                    self._snapshot[path] = (st.st_mtime, st.st_size)
                except OSError:
                    pass

    def changed_files(self):
        changed = []
        for root, dirs, files in os.walk(self.workspace):
            if ".bid" in root:
                continue
            for f in files:
                path = os.path.join(root, f)
                try:
                    st = os.stat(path)
                    prev = self._snapshot.get(path)
                    if prev is None or prev != (st.st_mtime, st.st_size):
                        changed.append(os.path.relpath(path, self.workspace))
                except OSError:
                    pass
        return changed

    def task_is_checked(self):
        todo_path = os.path.join(self.workspace, "docs/todo.md")
        if not os.path.exists(todo_path):
            return False
        with open(todo_path) as f:
            tasks = todo_mod.parse_todo(f.read())
        for t in tasks:
            if t["number"] == self.task_number:
                return t["checked"]
        return False

    def task_just_became_checked(self):
        now = self.task_is_checked()
        if now and not self.task_was_checked:
            self.task_was_checked = True
            return True
        self.task_was_checked = now
        return False

    def seen_done(self, text):
        if not text:
            return False
        lines = text.strip().split("\n")
        for line in reversed(lines):
            if line.strip() == "Done":
                return True
        return False

    def mark_activity(self):
        self.last_activity = time.time()

    def elapsed(self):
        return time.time() - self.start_time

    def inactive_for(self):
        return time.time() - self.last_activity
