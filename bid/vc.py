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
        if os.path.isdir(self.bid_dir):
            shutil.rmtree(self.bid_dir)
        os.makedirs(self.states_dir, exist_ok=True)
        self._save_snapshot("s0")
        self._set_current("s0")
        self._append_log("s0", "Initial project state")

    def get_current(self):
        if not os.path.exists(self.current_file):
            return None
        with open(self.current_file, encoding="utf-8") as file:
            return file.read().strip()

    def save_state(self, agent_name, description):
        states = self._list_states()
        next_number = max((int(state[1:]) for state in states), default=-1) + 1
        name = f"s{next_number}"
        self._save_snapshot(name)
        self._set_current(name)
        self._append_log(name, f"{agent_name} completed.\n{description}")
        return name

    def restore(self, state_name):
        snapshot = os.path.join(self.states_dir, state_name)
        if not os.path.isdir(snapshot):
            raise ValueError(f"state {state_name} not found")

        for item in os.listdir(self.workspace):
            if item == ".bid":
                continue
            path = os.path.join(self.workspace, item)
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)

        for item in os.listdir(snapshot):
            source = os.path.join(snapshot, item)
            destination = os.path.join(self.workspace, item)
            if os.path.isdir(source):
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)

        self._set_current(state_name)
        self._truncate_log(state_name)
        target_number = int(state_name[1:])
        for state in self._list_states():
            if int(state[1:]) > target_number:
                shutil.rmtree(os.path.join(self.states_dir, state))

    def get_log(self):
        if not os.path.exists(self.log_file):
            return "(no log)"
        with open(self.log_file, encoding="utf-8") as file:
            return file.read().rstrip()

    def _save_snapshot(self, name):
        destination = os.path.join(self.states_dir, name)
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
