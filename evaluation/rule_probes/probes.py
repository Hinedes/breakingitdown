"""Rule-following probes: how often does the real model obey each prompt-level rule in bid/rules.py?

Graders are pure functions over what the model said, so test_probes.py can prove them offline against
known-good and known-bad samples before any live number is trusted. Live runs: run_probes.py.
"""
import os
import re
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bid import adapter, harness  # noqa: E402

PATHISH = re.compile(r"[\w-]+/[\w./-]+|[\w-]+\.[A-Za-z0-9]{1,6}\b")

# Synthetic issues, one with code in the body (the shape that made the real Manager add a fence).
ISSUES = (
    "`slugify('Hello, world!')` returns 'hello-world-' but the slug must never end with a hyphen.",
    "The CLI crashes with a KeyError when the config file has no 'retries' key; it should default to 3.\n\n"
    "```python\ncfg = load('app.toml')\nprint(cfg['retries'])\n```",
    "`parse_duration('1h30m')` returns 90 instead of 5400 because hours are treated as minutes.",
)

# A small repository whose bug lives in a file the task never names.
FIXTURE = {
    "shop/__init__.py": "",
    "shop/cart.py": "def total(items):\n    return sum(i['price'] * i['qty'] for i in items)\n",
    "shop/config.py": "def load(path):\n    return {}\n",
    "shop/text/__init__.py": "",
    "shop/text/format.py": "def money(cents):\n    return f'{cents / 100:.2f}'\n",
    "shop/text/slugs.py": (
        "import re\n\n\ndef slugify(title):\n"
        "    return re.sub(r'[^a-z0-9]+', '-', title.lower())\n"
    ),
    "tests/test_cart.py": "from shop.cart import total\n\n\ndef test_total():\n    assert total([]) == 0\n",
}
WORKER_TASK = "Fix slugify so the slug it returns never ends with a hyphen."
WORKER_ISSUE = "slugify('Hello, world!') returns 'hello-world-'. The slug must never end with a hyphen."


class Recording:
    """Forwards to a backend and keeps (last message the model saw, what it replied)."""

    def __init__(self, inner):
        self.inner = inner
        self.turns = []

    def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
        reply = self.inner.run(messages, tools, max_tokens, timeout=timeout, grammar=grammar)
        self.turns.append((messages[-1]["content"], reply.get("content") or ""))
        return reply


def grade_manager(plan_text, attempts):
    items = [line for line in plan_text.splitlines() if line.startswith("- [ ]")]
    return {
        "items": len(items),
        "first_attempt": attempts == 1,
        "names_file": bool(items) and all(PATHISH.search(item) for item in items),
    }


def grade_worker(turns):
    single, missing_run, max_missing_run = 0, 0, 0
    for shown, reply in turns:
        if len(adapter._parse_content_into_turns(reply.strip(), None, "tools")) == 1:
            single += 1
        missing_run = missing_run + 1 if "file not found:" in shown else 0
        max_missing_run = max(max_missing_run, missing_run)
    graded = {
        "responses": len(turns),
        "single_command": single,
        "max_missing_run": max_missing_run,
        "found_target": any("def slugify" in shown for shown, _ in turns),
    }
    if len(turns) >= 60:  # a runaway trial: keep a few turns from its middle so the loop can be read
        graded["loop_sample"] = [(shown[:160], reply[:160]) for shown, reply in turns[40:46]]
    return graded


def probe_config(workspace, endpoint, model_name, seed):
    return {
        "endpoint": endpoint, "model_name": model_name, "max_tokens": 1024, "workspace": workspace,
        "request_timeout": 120, "inactivity_timeout": 120, "worker_timeout": 240, "repeat_action_limit": 5,
        "max_searches_per_worker": 0, "allow_host_run": False, "repo_context_mode": "tools",
        "protocol_grammar": False, "seed": seed, "task_statement": WORKER_ISSUE,
    }


def run_manager(make_backend, trial, issues=ISSUES):
    with tempfile.TemporaryDirectory() as tmp:
        backend = Recording(make_backend(trial))
        issue = issues[trial % len(issues)]
        result = harness.init_project(issue, probe_config(tmp, "", "", trial), backend)
        plan = ""
        if result.get("status") == "success":
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                plan = file.read()
        return grade_manager(plan, len(backend.turns))


def run_worker(make_backend, trial):
    with tempfile.TemporaryDirectory() as tmp:
        for rel, body in FIXTURE.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)) or tmp, exist_ok=True)
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as file:
                file.write(body)
        os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
        with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
            file.write(f"- [ ] T1 — {WORKER_TASK}\n")
        harness.ensure_workspace(tmp)
        backend = Recording(make_backend(trial))
        adapter.WorkerAdapter(probe_config(tmp, "", "", trial), 1).run(backend)
        return grade_worker(backend.turns)


# probe name -> (rule id, runner, does one trial pass?)
PROBES = {
    "manager_names_target_file": ("manager.names_target_file", run_manager, lambda r: r["names_file"]),
    "worker_one_command": ("worker.one_command_per_response", run_worker,
                           lambda r: r["responses"] > 0 and r["single_command"] == r["responses"]),
}
