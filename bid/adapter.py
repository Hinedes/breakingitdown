import os
import re

from . import todo as todo_mod


class ManagerInitAdapter:
    """BID reads manager.md + task.md, model returns plain Markdown TODO, BID writes."""

    RETRY_LIMIT = 3

    def __init__(self, config):
        self.config = config
        self.workspace = config["workspace"]

    def run(self, backend):
        manager_md = self._read("docs/manager.md")
        task_md = self._read("docs/task.md")

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
            content = self._clean(content)

            if self._valid(content):
                self._write("docs/todo.md", content)
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

    def _read(self, rel):
        path = os.path.join(self.workspace, rel)
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def _write(self, rel, content):
        path = os.path.join(self.workspace, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _clean(text):
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()
        return text

    @staticmethod
    def _valid(text):
        if not text:
            return False
        tasks = todo_mod.parse_todo(text)
        return len(tasks) >= 1
