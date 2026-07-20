import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid import todo


TODO_SAMPLE = """# TODO

- [ ] T1 — Research backends
- [ ] T2 — Compare backends
- [ ] T3 — Produce recommendation
"""

TODO_MIXED = """- [ ] T1 — First
- [x] T2 — Second
- [ ] T3 — Third
"""


def test_parse_todo():
    tasks = todo.parse_todo(TODO_SAMPLE)
    assert len(tasks) == 3
    assert tasks[0]["id"] == "T1"
    assert tasks[0]["number"] == 1
    assert tasks[0]["checked"] is False
    assert tasks[1]["id"] == "T2"
    assert tasks[2]["id"] == "T3"


def test_parse_checked():
    tasks = todo.parse_todo(TODO_MIXED)
    assert tasks[0]["checked"] is False
    assert tasks[1]["checked"] is True
    assert tasks[2]["checked"] is False


def test_get_task():
    tasks = todo.parse_todo(TODO_SAMPLE)
    t = todo.get_task(tasks, 2)
    assert t is not None
    assert t["id"] == "T2"
    assert todo.get_task(tasks, 99) is None


def test_first_unchecked():
    tasks = todo.parse_todo(TODO_MIXED)
    assert todo.first_unchecked(tasks)["number"] == 1
    all_done = todo.parse_todo("- [x] T1 — done\n- [x] T2 — done")
    assert todo.first_unchecked(all_done) is None


def test_all_checked():
    tasks = todo.parse_todo(TODO_MIXED)
    assert todo.all_checked(tasks) is False
    all_done = todo.parse_todo("- [x] T1 — done\n- [x] T2 — done")
    assert todo.all_checked(all_done) is True


def test_set_task_checked():
    result = todo.set_task_checked(TODO_SAMPLE, 1, checked=True)
    tasks = todo.parse_todo(result)
    assert tasks[0]["checked"] is True
    assert tasks[1]["checked"] is False


def test_set_task_unchecked():
    result = todo.set_task_checked(TODO_MIXED, 2, checked=False)
    tasks = todo.parse_todo(result)
    assert tasks[0]["checked"] is False
    assert tasks[1]["checked"] is False


def test_set_task_only_affects_target():
    result = todo.set_task_checked(TODO_SAMPLE, 2, checked=True)
    tasks = todo.parse_todo(result)
    assert tasks[0]["checked"] is False
    assert tasks[1]["checked"] is True
    assert tasks[2]["checked"] is False


def test_nonexistent_task():
    result = todo.set_task_checked(TODO_SAMPLE, 99, checked=True)
    assert result == TODO_SAMPLE


def test_various_separators():
    text = "- [ ] T1 - dash\n- [ ] T2 -- double dash\n- [ ] T3 — em dash"
    tasks = todo.parse_todo(text)
    assert len(tasks) == 3
    assert tasks[0]["description"] == "dash"
    assert tasks[2]["description"] == "em dash"
