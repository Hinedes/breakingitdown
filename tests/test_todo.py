from bid import todo


TODO_SAMPLE = """# TODO

- [ ] T1 — Research backends
- [x] T2 — Compare backends
- [ ] T3 — Produce recommendation
"""


def test_parse_todo():
    tasks = todo.parse_todo(TODO_SAMPLE)
    assert [task["id"] for task in tasks] == ["T1", "T2", "T3"]
    assert [task["checked"] for task in tasks] == [False, True, False]
    assert todo.first_unchecked(tasks)["number"] == 1
    assert not todo.all_checked(tasks)


def test_set_task_checked_only_affects_target():
    result = todo.set_task_checked(TODO_SAMPLE, 1, checked=True)
    tasks = todo.parse_todo(result)
    assert [task["checked"] for task in tasks] == [True, True, False]

    result = todo.set_task_checked(result, 1, checked=False)
    tasks = todo.parse_todo(result)
    assert [task["checked"] for task in tasks] == [False, True, False]
