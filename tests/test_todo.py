from bid import todo


TODO_SAMPLE = """# TODO

- [ ] T1 — Research backends
  Output: docs/work/T1.md
  Inputs: docs/task.md
  Accept: Summarize viable backends.
- [ ] T2 — Compare backends
  Output: docs/work/T2.md
  Inputs: docs/work/T1.md
  Accept: Recommend one backend.
- [ ] T3 — Produce recommendation
  Output: docs/work/T3.md
  Inputs: docs/work/T1.md, docs/work/T2.md
  Accept: State the recommendation.
"""

TODO_MIXED = """- [ ] T1 — First
- [x] T2 — Second
- [ ] T3 — Third
"""


def test_parse_todo():
    tasks = todo.parse_todo(TODO_SAMPLE)
    assert [task["id"] for task in tasks] == ["T1", "T2", "T3"]
    assert tasks[0]["number"] == 1
    assert not tasks[0]["checked"]
    assert tasks[0]["output"] == "docs/work/T1.md"
    assert tasks[0]["accept"] == "Summarize viable backends."


def test_parse_checked():
    tasks = todo.parse_todo(TODO_MIXED)
    assert [task["checked"] for task in tasks] == [False, True, False]


def test_get_and_first_unchecked():
    tasks = todo.parse_todo(TODO_MIXED)
    assert todo.get_task(tasks, 2)["id"] == "T2"
    assert todo.get_task(tasks, 99) is None
    assert todo.first_unchecked(tasks)["number"] == 1


def test_all_checked_requires_tasks():
    assert todo.all_checked(todo.parse_todo("- [x] T1 — done\n- [x] T2 — done"))
    assert not todo.all_checked([])


def test_set_task_checked_only_affects_target():
    result = todo.set_task_checked(TODO_SAMPLE, 2, checked=True)
    tasks = todo.parse_todo(result)
    assert [task["checked"] for task in tasks] == [False, True, False]


def test_worker_can_check_own_task():
    updated = todo.set_task_checked(TODO_SAMPLE, 1, True)
    allowed, reason = todo.validate_worker_todo_update(TODO_SAMPLE, updated, 1)
    assert allowed, reason


def test_worker_can_backtrack_by_unchecking_own_task():
    checked = todo.set_task_checked(TODO_SAMPLE, 1, True)
    unchecked = todo.set_task_checked(checked, 1, False)
    allowed, reason = todo.validate_worker_todo_update(checked, unchecked, 1)
    assert allowed, reason


def test_worker_cannot_check_other_task():
    updated = todo.set_task_checked(TODO_SAMPLE, 2, True)
    allowed, reason = todo.validate_worker_todo_update(TODO_SAMPLE, updated, 1)
    assert not allowed
    assert "T1" in reason


def test_worker_cannot_rewrite_description():
    updated = TODO_SAMPLE.replace("Research backends", "Delete everything")
    allowed, reason = todo.validate_worker_todo_update(TODO_SAMPLE, updated, 1)
    assert not allowed


def test_worker_cannot_add_or_remove_tasks():
    updated = TODO_SAMPLE + "- [ ] T4 — Extra\n"
    allowed, reason = todo.validate_worker_todo_update(TODO_SAMPLE, updated, 1)
    assert not allowed
