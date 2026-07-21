import os
import re
import shutil
import tempfile
import time
from datetime import date


_LOCK_TIMEOUT = 30


class VersionControl:
    def __init__(self, workspace_root):
        self.workspace = os.path.realpath(workspace_root)
        self.bid_dir = os.path.join(self.workspace, ".bid")
        self.states_dir = os.path.join(self.bid_dir, "states")
        self.current_file = os.path.join(self.bid_dir, "current")
        self.log_file = os.path.join(self.bid_dir, "log.md")
        self.lock_file = os.path.join(self.bid_dir, "lock")

    def _acquire_lock(self):
        os.makedirs(self.bid_dir, exist_ok=True)
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while time.monotonic() < deadline:
            try:
                fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                time.sleep(0.1)
        raise RuntimeError(f"could not acquire VC lock within {_LOCK_TIMEOUT}s")

    def _release_lock(self):
        try:
            os.remove(self.lock_file)
        except OSError:
            pass

    def init(self):
        if os.path.isdir(self.bid_dir):
            shutil.rmtree(self.bid_dir)
        os.makedirs(self.states_dir, exist_ok=True)
        self._acquire_lock()
        try:
            self._save_snapshot_atomic("s0")
            self._set_current("s0")
            self._append_log("s0", "Initial project state")
        finally:
            self._release_lock()

    def get_current(self):
        if not os.path.exists(self.current_file):
            return None
        with open(self.current_file, encoding="utf-8") as file:
            return file.read().strip()

    def save_state(self, agent_name, description):
        self._acquire_lock()
        try:
            states = self._list_states()
            next_number = max((int(state[1:]) for state in states), default=-1) + 1
            name = f"s{next_number}"
            return self._commit_state(name, agent_name, description)
        finally:
            self._release_lock()

    def _commit_state(self, name, agent_name, description):
        self._validate_state_name(name)
        self._save_snapshot_atomic(name)
        self._set_current(name)
        self._append_log(name, f"{agent_name} completed.\n{description}")
        return name

    @staticmethod
    def _validate_state_name(name):
        if not re.fullmatch(r"s\d+", name):
            raise ValueError(f"invalid state name: {name!r}")

    def restore(self, state_name):
        if not re.fullmatch(r"s\d+", state_name):
            raise ValueError(f"invalid state name: {state_name!r}")
        snapshot = os.path.realpath(os.path.join(self.states_dir, state_name))
        if not snapshot.startswith(os.path.realpath(self.states_dir) + os.sep):
            raise ValueError(f"state path traversal denied: {state_name!r}")
        if not os.path.isdir(snapshot):
            raise ValueError(f"state {state_name} not found")

        self._acquire_lock()
        try:
            # Build restore tree in a temp location, then swap
            restore_tmp = tempfile.mkdtemp(dir=self.bid_dir, prefix="restore_")
            try:
                for item in os.listdir(snapshot):
                    src = os.path.join(snapshot, item)
                    dst = os.path.join(restore_tmp, item)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)

                # Delete live workspace contents (preserve .bid)
                for item in os.listdir(self.workspace):
                    if item == ".bid":
                        continue
                    path = os.path.join(self.workspace, item)
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)

                # Move restore tree into workspace
                for item in os.listdir(restore_tmp):
                    shutil.move(os.path.join(restore_tmp, item), self.workspace)
            finally:
                shutil.rmtree(restore_tmp, ignore_errors=True)

            self._set_current(state_name)
            self._truncate_log(state_name)
            target_number = int(state_name[1:])
            for state in self._list_states():
                if int(state[1:]) > target_number:
                    shutil.rmtree(os.path.join(self.states_dir, state))
        finally:
            self._release_lock()

    def get_log(self):
        if not os.path.exists(self.log_file):
            return "(no log)"
        with open(self.log_file, encoding="utf-8") as file:
            return file.read().rstrip()

    def _save_snapshot_atomic(self, name):
        destination = os.path.join(self.states_dir, name + ".tmp")
        if os.path.isdir(destination):
            shutil.rmtree(destination)
        os.makedirs(destination, exist_ok=True)
        for item in os.listdir(self.workspace):
            if item == ".bid":
                continue
            source = os.path.join(self.workspace, item)
            target = os.path.join(destination, item)
            if os.path.isdir(source):
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        final = os.path.join(self.states_dir, name)
        if os.path.isdir(final):
            shutil.rmtree(final)
        os.rename(destination, final)

    def _set_current(self, name):
        with open(self.current_file, "w", encoding="utf-8") as file:
            file.write(name + "\n")

    def _list_states(self):
        if not os.path.isdir(self.states_dir):
            return []
        states = [
            name
            for name in os.listdir(self.states_dir)
            if name.startswith("s")
            and name[1:].isdigit()
            and os.path.isdir(os.path.join(self.states_dir, name))
        ]
        return sorted(states, key=lambda name: int(name[1:]))

    def _append_log(self, name, description):
        today = date.today().strftime("%d/%m/%Y")
        with open(self.log_file, "a", encoding="utf-8") as file:
            file.write(f"\n## {today}\n\n### {name}\n{description}\n")

    def _truncate_log(self, state_name):
        if not os.path.exists(self.log_file):
            return
        with open(self.log_file, encoding="utf-8") as file:
            content = file.read()
        marker = f"### {state_name}\n"
        start = content.rfind(marker)
        if start < 0:
            return
        next_state = content.find("\n### s", start + len(marker))
        next_date = content.find("\n## ", start + len(marker))
        cut_candidates = [index for index in (next_state, next_date) if index >= 0]
        if cut_candidates:
            content = content[: min(cut_candidates)]
        with open(self.log_file, "w", encoding="utf-8") as file:
            file.write(content.rstrip() + "\n")
