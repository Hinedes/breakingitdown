import os
import shutil
from datetime import date


class VersionControl:
    def __init__(self, workspace_root):
        self.workspace = os.path.realpath(workspace_root)
        self.bid_dir = os.path.join(self.workspace, ".bid")
        self.states_dir = os.path.join(self.bid_dir, "states")
        self.current_file = os.path.join(self.bid_dir, "current")
        self.log_file = os.path.join(self.bid_dir, "log.md")

    def init(self):
        os.makedirs(self.states_dir, exist_ok=True)
        self._save_snapshot("s0")
        self._set_current("s0")
        self._append_log("s0", "Initial project state")

    def get_current(self):
        if os.path.exists(self.current_file):
            with open(self.current_file) as f:
                return f.read().strip()
        return None

    def save_state(self, agent_name, description):
        states = sorted(self._list_states())
        if states:
            next_num = max(int(s[1:]) for s in states) + 1
        else:
            next_num = 0
        name = f"s{next_num}"
        self._save_snapshot(name)
        self._set_current(name)
        self._append_log(name, f"{agent_name} completed.\n{description}")
        return name

    def restore(self, state_name):
        snap = os.path.join(self.states_dir, state_name)
        if not os.path.isdir(snap):
            raise ValueError(f"state {state_name} not found")
        for item in os.listdir(self.workspace):
            if item == ".bid":
                continue
            item_path = os.path.join(self.workspace, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path)
            else:
                os.remove(item_path)
        for item in os.listdir(snap):
            src = os.path.join(snap, item)
            dst = os.path.join(self.workspace, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        self._set_current(state_name)
        self._truncate_log(state_name)
        later_states = [s for s in self._list_states()
                        if int(s[1:]) > int(state_name[1:])]
        for s in later_states:
            shutil.rmtree(os.path.join(self.states_dir, s))

    def get_log(self):
        if os.path.exists(self.log_file):
            with open(self.log_file) as f:
                return f.read().rstrip()
        return "(no log)"

    def _save_snapshot(self, name):
        dest = os.path.join(self.states_dir, name)
        os.makedirs(dest, exist_ok=True)
        for item in os.listdir(self.workspace):
            if item == ".bid":
                continue
            item_path = os.path.join(self.workspace, item)
            dest_path = os.path.join(dest, item)
            if os.path.isdir(item_path):
                shutil.copytree(item_path, dest_path, dirs_exist_ok=True)
            else:
                shutil.copy2(item_path, dest_path)

    def _set_current(self, name):
        with open(self.current_file, "w") as f:
            f.write(name + "\n")

    def _list_states(self):
        if not os.path.isdir(self.states_dir):
            return []
        return [d for d in os.listdir(self.states_dir)
                if d.startswith("s") and os.path.isdir(os.path.join(self.states_dir, d))]

    def _append_log(self, name, description):
        today = date.today().strftime("%d/%m/%Y")
        with open(self.log_file, "a") as f:
            f.write(f"\n## {today}\n\n### {name}\n{description}\n")

    def _truncate_log(self, state_name):
        if not os.path.exists(self.log_file):
            return
        with open(self.log_file) as f:
            content = f.read()
        marker = f"### {state_name}"
        idx = content.rfind(marker)
        if idx >= 0:
            next_idx = content.find("\n## ", idx + len(marker) + 1)
            if next_idx >= 0:
                content = content[:next_idx]
            with open(self.log_file, "w") as f:
                f.write(content.rstrip() + "\n")
