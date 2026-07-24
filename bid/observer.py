import hashlib
import os
import time


class Observer:
    def __init__(self, workspace, task_number=None):
        self.workspace = workspace
        self.task_number = task_number
        self._snapshot = self._tree_state()
        self.last_activity = time.time()

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

    def mark_activity(self):
        self.last_activity = time.time()

    def inactive_for(self):
        return time.time() - self.last_activity
