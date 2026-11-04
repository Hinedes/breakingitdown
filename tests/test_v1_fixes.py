"""Regressions from the v1 SWE-bench pilot: plan parsing, path guessing, and the loop that burned the budget."""
import tempfile

from bid import adapter, harness, model, todo
from tests.test_repo_context import prepare_worker, run_worker, worker_config
from tests.test_worker_continuity import config, text_response

# Real Manager answer from pylint-dev__pylint-7277: a sensible one-task plan with a fenced snippet under it.
FENCED_PLAN = (
    "- [ ] Modify `pylint/__init__.py` to check sys.path[0] before removing it, with the following content:\n"
    "\n"
    "```python\n"
    "if sys.path[0] in (\"\", \".\", os.getcwd()):\n"
    "    sys.path.pop(0)\n"
    "```\n"
)


def test_manager_folds_a_fenced_snippet_into_its_task():
    plan = adapter.ManagerInitAdapter._todo(FENCED_PLAN)
    assert plan.count("\n") == 0 and plan.startswith("- [ ] T1 — Modify `pylint/__init__.py`")
    assert "sys.path.pop(0)" in plan and "```" not in plan


def test_manager_fenced_plan_initialises_a_project():
    with tempfile.TemporaryDirectory() as tmp:
        result = harness.init_project("Fix pylint", config(tmp), backend=model.MockBackend([text_response(FENCED_PLAN)]))
        assert result["status"] == "success"
        with open(f"{tmp}/docs/todo.md", encoding="utf-8") as file:
            tasks = todo.parse_todo(file.read())
        assert [task["id"] for task in tasks] == ["T1"] and "sys.path.pop(0)" in tasks[0]["description"]


def test_manager_still_rejects_commentary_around_a_fence():
    assert adapter.ManagerInitAdapter._todo("```\ncode\n```\n- [ ] First") is None  # fence before any item
    assert adapter.ManagerInitAdapter._todo(FENCED_PLAN + "Note: also do this.") is None
    assert adapter.ManagerInitAdapter._todo("- [ ] First\n```\n- [ ] not a task\n```") == "- [ ] T1 — First - [ ] not a task"


def test_second_missing_file_in_a_row_points_to_navigation_tools():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        _, backend = run_worker(workspace, "tools", ["READ a.py", "READ b.py", "Done"])
        assert "Do not guess paths" not in backend.call_history[1]["messages"][-1]["content"]
        hint = backend.call_history[2]["messages"][-1]["content"]
        assert "file not found: b.py" in hint and "Use FIND <literal>" in hint and "MAP <directory>" in hint


def test_missing_file_hint_never_names_commands_that_are_off():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        _, backend = run_worker(workspace, "off", ["READ a.py", "READ b.py", "Done"], max_searches_per_worker=0)
        hint = backend.call_history[2]["messages"][-1]["content"]
        assert "Do not guess paths. Use only paths named in the task." in hint
        assert "FIND" not in hint and "MAP" not in hint


def test_a_run_of_different_missing_paths_is_a_bounded_stall():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        guesses = [f"READ migrations/{index:04d}_guess.py" for index in range(60)]
        result, backend = run_worker(workspace, "tools", guesses)
        assert result["status"] == "stalled"
        assert len(backend.call_history) < 30  # the pilot run made 76 requests before the token budget ended it


def test_a_successful_read_resets_the_missing_file_run():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        _, backend = run_worker(workspace, "tools", ["READ a.py", "READ docs/todo.md", "READ b.py", "Done"])
        assert "Do not guess paths" not in backend.call_history[3]["messages"][-1]["content"]


def test_rework_on_an_escalated_task_is_read_as_a_rewrite():
    # Real Manager behaviour (v1 pilot): told to use # Replace Remaining Plan, it answers `# Rework T1`.
    from tests.test_phase1_gaps import ACCEPT, REWORK, WRITE_BAD, start
    from tests.test_worker_continuity import manager_response, todo_item
    backend = model.MockBackend([
        text_response(WRITE_BAD), text_response(REWORK),
        text_response(WRITE_BAD), text_response(REWORK),
        manager_response(rework=[1, "a.txt must contain exactly ok"], project="CONTINUE"),
        text_response("WRITE a.txt\nok\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[1]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt"), max_task_reworks=1)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        with open(f"{tmp}/docs/todo.md", encoding="utf-8") as file:
            assert "Required: a.txt must contain exactly ok" in file.read()


def test_escalation_rewrite_keeps_other_unchecked_tasks_and_rejects_other_reworks():
    tasks = todo.parse_todo("- [ ] T1 — First\n- [ ] T2 — Second\n- [ ] T3 — Third\n")
    decision = {"done": [], "rework": {"task": 2, "reason": "needs\nmore"}, "project": "CONTINUE"}
    out = harness._escalated_rework_as_replace(decision, tasks, {}, 2)
    assert out["rework"] is None and out["replace"] == ["First", "Second Required: needs more", "Third"]
    assert harness._escalated_rework_as_replace(decision, tasks, {}, 1) is decision     # not the escalated task
    assert harness._escalated_rework_as_replace(decision, tasks, {2: {}}, 2) is decision  # provisional: normal rework
    both = dict(decision, add=["x"])
    assert harness._escalated_rework_as_replace(both, tasks, {}, 2) is both              # ambiguous: left to validation


def test_a_cycle_of_different_errors_is_a_bounded_stall():
    # Real Worker loop from the rule probes: three different failing commands in turn never repeat the
    # previous turn, so only the consecutive no-progress bound can end it (the probe ran to 279 responses).
    cycle = [
        "REPLACE shop/x.py\nfoo\nEND REPLACE",                      # missing ---REPLACE_WITH---
        "REPLACE shop/x.py\n---REPLACE_WITH---\nbar\nEND REPLACE",  # old_text required
        "READ shop/x.py\nfoo\nEND READ",                            # unknown command END READ
    ]
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        result, backend = run_worker(workspace, "tools", cycle * 40)
        assert result["status"] == "stalled"
        assert len(backend.call_history) < 60  # repeat_action_limit 3: six idle turns per reset, three resets


def test_progress_resets_the_no_progress_count():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        def failing(tag):  # five distinct errors: one short of the bound (repeat_action_limit 3)
            return [f"REPLACE shop/{tag}{i}.py\nfoo\nEND REPLACE" for i in range(5)]

        script = failing("a") + ["READ docs/todo.md"] + failing("b") + ["WRITE a.txt\nA\nEND WRITE", "Done"]
        result, _ = run_worker(workspace, "tools", script)
        assert result["status"] == "done"
