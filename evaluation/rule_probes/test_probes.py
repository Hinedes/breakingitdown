"""Prove the probe graders on known-good and known-bad samples before trusting any live number."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probes
from bid.model import MockBackend


def reply(text):
    return {"role": "assistant", "content": text, "tool_calls": None, "finish_reason": "stop"}


def test_manager_grader_separates_good_and_bad_plans():
    good = "- [ ] T1 — Edit `shop/text/slugs.py` so slugify strips a trailing hyphen\n"
    assert probes.grade_manager(good, 1) == {"items": 1, "first_attempt": True, "names_file": True}
    assert probes.grade_manager("- [ ] T1 — Fix the slug bug\n", 1)["names_file"] is False
    assert probes.grade_manager(good, 3)["first_attempt"] is False
    assert probes.grade_manager("", 3)["names_file"] is False


def test_worker_grader_counts_commands_and_missing_runs():
    turns = [
        ("task", "READ a.py"), ("file not found: a.py", "READ b.py"),
        ("file not found: b.py", "FIND slugify"), ("hits...", "READ one.py\nREAD two.py"),
        ("def slugify(x)", "Done"),
    ]
    graded = probes.grade_worker(turns)
    assert graded == {"responses": 5, "single_command": 4, "max_missing_run": 2, "found_target": True}


def test_manager_probe_end_to_end_on_a_scripted_model():
    plan = "- [ ] Modify `app/config.py` to default retries to 3, with:\n\n```python\ncfg.get('retries', 3)\n```\n"
    result = probes.run_manager(lambda trial: MockBackend([reply(plan)]), 1)
    assert result["first_attempt"] and result["names_file"]


def test_worker_probe_end_to_end_on_a_scripted_model():
    script = ["READ guess.py", "READ other.py", "FIND slugify", "READ shop/text/slugs.py", "Done"]
    result = probes.run_worker(lambda trial: MockBackend([reply(text) for text in script]), 0)
    assert result["found_target"] and result["max_missing_run"] == 2 and result["single_command"] == result["responses"]
