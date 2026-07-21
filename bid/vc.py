import json
import os
import re
import shutil
import tempfile
import time
from datetime import date


_LOCK_TIMEOUT = 30.0
_LOCK_STALE_SECONDS = 120.0


class VersionControl:
    def __init__(self, workspace_root):
        self.workspace = os.path.realpath(workspace_root)
        self.bid_dir = os.path.join(self.workspace, ".bid")
        self.states_dir = os.path.join(self.bid_dir, "states")
        self.current_file = os.path.join(self.bid_dir, "current")
        self.log_file = os.path.join(self.bid_dir, "log.md")
        self.lock_file = os.path.join(self.bid_dir, "lock")
        self._lock_token = None

    @staticmethod
    def _pid_is_alive(pid):
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _read_lock(self):
        try:
            with open(self.lock_file, encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _lock_is_stale(self):
        try:
            age = time.time() - os.path.getmtime(self.lock_file)
        except OSError:
            return True
        metadata = self._read_lock()
        pid = metadata.get("pid")
        return age > _LOCK_STALE_SECONDS or not self._pid_is_alive(pid)

    def _acquire_lock(self):
        os.makedirs(self.bid_dir, exist_ok=True)
        deadline = time.monotonic() + _LOCK_TIMEOUT
        token = f"{os.getpid()}-{time.time_ns()}"
        payload = json.dumps({"pid": os.getpid(), "created": time.time(), "token": token})
        while True:
            try:
                descriptor = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                self._lock_token = token
                return
            except FileExistsError:
                if self._lock_is_stale():
                    try:
                        os.remove(self.lock_file)
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"could not acquire VC lock within {_LOCK_TIMEOUT:g}s")
                time.sleep(0.1)

    def _release_lock(self):
        try:
            metadata = self._read_lock()
            if self._lock_token and metadata.get("token") == self._lock_token:
                os.remove(self.lock_file)
        except OSError:
            pass
        finally:
            self._lock_token = None

    @staticmethod
    def _write_text_atomic(path, content):
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
        with open(self.current_file, encoding="utf-8") as handle:
            return handle.read().strip()

    def save_state(self, agent_name, description):
        self._acquire_lock()
        try:
            states = self._list_states()
            next_number = max((int(state[1:]) for state in states), default=-1) + 1
            return self._commit_state(f"s{next_number}", agent_name, description)
        finally:
            self._release_lock()

    def _commit_state(self, name, agent_name, description):
        self._validate_state_name(name)
        self._save_snapshot_atomic(name)
        try:
            self._set_current(name)
            self._append_log(name, f"{agent_name} completed.\n{description}")
        except BaseException:
            shutil.rmtree(os.path.join(self.states_dir, name), ignore_errors=True)
            raise
        return name

    @staticmethod
    def _validate_state_name(name):
        if not re.fullmatch(r"s\d+", name):
            raise ValueError(f"invalid state name: {name!r}")

    def restore(self, state_name):
        self._validate_state_name(state_name)
        snapshot = os.path.realpath(os.path.join(self.states_dir, state_name))
        states_root = os.path.realpath(self.states_dir)
        if not snapshot.startswith(states_root + os.sep):
            raise ValueError(f"state path traversal denied: {state_name!r}")
        if not os.path.isdir(snapshot):
            raise ValueError(f"state {state_name} not found")

        self._acquire_lock()
        restore_tree = tempfile.mkdtemp(dir=self.bid_dir, prefix="restore-new-")
        backup_tree = tempfile.mkdtemp(dir=self.bid_dir, prefix="restore-old-")
        moved_new = []
        try:
            for item in os.listdir(snapshot):
                source = os.path.join(snapshot, item)
                target = os.path.join(restore_tree, item)
                if os.path.isdir(source):
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)

            live_items = [item for item in os.listdir(self.workspace) if item != ".bid"]
            for item in live_items:
                shutil.move(os.path.join(self.workspace, item), os.path.join(backup_tree, item))

            try:
                for item in os.listdir(restore_tree):
                    shutil.move(os.path.join(restore_tree, item), os.path.join(self.workspace, item))
                    moved_new.append(item)
                self._set_current(state_name)
                self._truncate_log(state_name)
            except BaseException:
                for item in moved_new:
                    path = os.path.join(self.workspace, item)
                    if os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                for item in os.listdir(backup_tree):
                    shutil.move(os.path.join(backup_tree, item), os.path.join(self.workspace, item))
                raise

            target_number = int(state_name[1:])
            for state in self._list_states():
                if int(state[1:]) > target_number:
                    shutil.rmtree(os.path.join(self.states_dir, state))
        finally:
            shutil.rmtree(restore_tree, ignore_errors=True)
            shutil.rmtree(backup_tree, ignore_errors=True)
            self._release_lock()

    def get_log(self):
        if not os.path.exists(self.log_file):
            return "(no log)"
        with open(self.log_file, encoding="utf-8") as handle:
            return handle.read().rstrip()

    def _save_snapshot_atomic(self, name):
        self._validate_state_name(name)
        os.makedirs(self.states_dir, exist_ok=True)
        temporary = tempfile.mkdtemp(dir=self.states_dir, prefix=f".{name}-")
        try:
            for item in os.listdir(self.workspace):
                if item == ".bid":
                    continue
                source = os.path.join(self.workspace, item)
                target = os.path.join(temporary, item)
                if os.path.isdir(source):
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
            final = os.path.join(self.states_dir, name)
            if os.path.exists(final):
                raise FileExistsError(f"state already exists: {name}")
            os.rename(temporary, final)
            temporary = None
        finally:
            if temporary:
                shutil.rmtree(temporary, ignore_errors=True)

    def _set_current(self, name):
        self._validate_state_name(name)
        self._write_text_atomic(self.current_file, name + "\n")

    def _list_states(self):
        if not os.path.isdir(self.states_dir):
            return []
        states = [
            name
            for name in os.listdir(self.states_dir)
            if re.fullmatch(r"s\d+", name)
            and os.path.isdir(os.path.join(self.states_dir, name))
        ]
        return sorted(states, key=lambda name: int(name[1:]))

    def _append_log(self, name, description):
        today = date.today().strftime("%d/%m/%Y")
        existing = ""
        if os.path.exists(self.log_file):
            with open(self.log_file, encoding="utf-8") as handle:
                existing = handle.read()
        updated = existing + f"\n## {today}\n\n### {name}\n{description}\n"
        self._write_text_atomic(self.log_file, updated)

    def _truncate_log(self, state_name):
        if not os.path.exists(self.log_file):
            return
        with open(self.log_file, encoding="utf-8") as handle:
            content = handle.read()
        marker = f"### {state_name}\n"
        start = content.rfind(marker)
        if start < 0:
            return
        next_state = content.find("\n### s", start + len(marker))
        next_date = content.find("\n## ", start + len(marker))
        cut_candidates = [index for index in (next_state, next_date) if index >= 0]
        if cut_candidates:
            content = content[: min(cut_candidates)]
        self._write_text_atomic(self.log_file, content.rstrip() + "\n")
