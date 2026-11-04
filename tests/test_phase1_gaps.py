"""Phase 1 gaps: Manager escalation, evidence gate, verification-only review, ratified-file visibility."""
import os
import subprocess
import tempfile

from bid import adapter, harness, model, vc
from tests.test_worker_continuity import (
    config,
    manager_response,
    prepare_workspace,
    text_response,
    todo_item,
)

WRITE_BAD = "WRITE a.txt\nbad\nEND WRITE\nDone"
REWORK = "REWORK\nReason: a.txt must say ok"
ACCEPT = "ACCEPT\nReason: ok"


def start(tmp, init_todo, **overrides):
    cfg = config(tmp, **overrides)
    assert harness.init_project("task", cfg, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
    return cfg


def reviewer_calls(backend):
    return [c for c in backend.call_history if c["messages"][0]["content"].startswith("You are BID Task Reviewer")]


def test_exhausted_task_escalates_to_manager_instead_of_ending_the_project():
    backend = model.MockBackend([
        text_response(WRITE_BAD), text_response(REWORK),
        text_response(WRITE_BAD), text_response(REWORK),          # second rejection passes the limit of 1
        manager_response(replace=["Write a.txt containing ok"], project="CONTINUE"),
        text_response("WRITE a.txt\nok\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[1]),                                # the replacement is numbered T1 again
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt"), max_task_reworks=1)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        with open(os.path.join(tmp, "a.txt"), encoding="utf-8") as file:
            assert file.read().strip() == "ok"
        manager_prompt = backend.call_history[4]["messages"][1]["content"]
        assert "ESCALATED" in manager_prompt and "T1" in manager_prompt and "a.txt must say ok" in manager_prompt
        assert "task_escalation: task=T1" in vc.VersionControl(tmp).get_log()
        # The re-planned task starts clean: it must not inherit the old task's Reviewer feedback.
        assert "Previous reviewer feedback" not in backend.call_history[5]["messages"][-1]["content"]


def test_escalations_are_bounded_and_the_stop_is_remembered():
    keep_going = manager_response(add=["Another task"], project="CONTINUE")
    backend = model.MockBackend([
        text_response(WRITE_BAD), text_response(REWORK), text_response(WRITE_BAD), text_response(REWORK), keep_going,
        text_response(WRITE_BAD), text_response(REWORK), text_response(WRITE_BAD), text_response(REWORK),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt"), max_task_reworks=1, max_manager_reworks=1)
        result = harness.run_project(cfg, backend=backend)
        assert result["status"] == "error" and "escalation" in result["reason"]
        again = harness.run_project(cfg, backend=model.MockBackend([]))
        assert again["status"] == "error"


def test_review_that_accepts_a_zero_test_run_is_turned_into_rework():
    outputs = iter([
        "collected 0 items\n\nno tests ran in 0.01s",
        "collected 1 item\n\n1 passed in 0.01s",
    ])

    def executor(argv, *, cwd, timeout):
        return subprocess.CompletedProcess(argv, 0, next(outputs), "")

    backend = model.MockBackend([
        text_response("WRITE test_x.py\ndef helper():\n    pass\nEND WRITE\nRUN pytest test_x.py\nDone"),
        text_response(ACCEPT),
        text_response("WRITE test_x.py\ndef test_ok():\n    assert True\nEND WRITE\nRUN pytest test_x.py\nDone"),
        text_response(ACCEPT),
        manager_response(done=[1]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Add a passing test"), run_executor=executor)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        retry_prompt = backend.call_history[2]["messages"][-1]["content"]
        assert "ran zero tests" in retry_prompt


def test_task_without_a_diff_shows_the_reviewer_the_recorded_run_output():
    def executor(argv, *, cwd, timeout):
        return subprocess.CompletedProcess(argv, 0, "VERIFIED-OK 3 passed", "")

    backend = model.MockBackend([
        text_response("RUN python -c pass\nDone"), text_response(ACCEPT), manager_response(done=[1]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Run the tests and confirm they pass"), run_executor=executor)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        prompt = reviewer_calls(backend)[0]["messages"][1]["content"]
        assert "VERIFIED-OK 3 passed" in prompt


def test_reviewer_is_told_when_a_candidate_rewrites_ratified_files():
    backend = model.MockBackend([
        text_response("WRITE a.txt\nfirst\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[1], project="CONTINUE"),
        text_response("WRITE a.txt\nchanged\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[2]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt") + todo_item(2, "Add b"), provisional_batch=1)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        first, second = (c["messages"][1]["content"] for c in reviewer_calls(backend))
        assert "ratified" not in first.lower()
        assert "a.txt (T1)" in second and "ratified" in second.lower()


def test_zero_test_check_sees_deselected_runs_and_later_real_runs():
    zero = {"command": "pytest -k typo", "stdout": "collected 12 items / 12 deselected\n\nno tests ran in 0.01s", "stderr": ""}
    real = {"command": "pytest", "stdout": "collected 12 items\n\n12 passed in 0.5s", "stderr": ""}
    assert harness._zero_test_command([zero]) == "pytest -k typo"
    assert harness._zero_test_command([real, zero]) == "pytest -k typo"
    assert harness._zero_test_command([zero, real]) is None
    assert harness._zero_test_command([{"command": "ls", "stdout": "a.txt", "stderr": ""}]) is None


def test_ratified_files_notice_reads_only_the_changed_files(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        prepare_workspace(tmp, todo_item(1, "Write a.txt") + todo_item(2, "Edit a.txt"))
        store = vc.VersionControl(tmp)
        base = store.get_current()
        with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as file:
            file.write("first\n")
        first = store.save_state("worker", "T1")
        store.append_log(f"ratified: task=T1 base={base} candidate={first}")
        with open(os.path.join(tmp, "a.txt"), "w", encoding="utf-8") as file:
            file.write("second\n")
        second = store.save_state("worker", "T2")

        calls = []
        real_tree = adapter._workspace_tree
        monkeypatch.setattr(adapter, "_workspace_tree", lambda root: calls.append(root) or real_tree(root))
        assert harness._ratified_files_touched(store, first, second) == "a.txt (T1)"
        # One base/candidate comparison only; each ratified task costs single-file reads, not a repo walk.
        assert len(calls) == 2


def test_escalation_while_another_task_is_provisional():
    backend = model.MockBackend([
        text_response("WRITE a.txt\nA\nEND WRITE\nDone"), text_response(ACCEPT),            # T1 provisional
        text_response("WRITE c.txt\nbad\nEND WRITE\nDone"), text_response(REWORK),
        text_response("WRITE c.txt\nbad\nEND WRITE\nDone"), text_response(REWORK),          # T2 exhausted
        manager_response(done=[1], replace=["Write b.txt"], project="CONTINUE"),
        text_response("WRITE b.txt\nok\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[2]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt") + todo_item(2, "Write c.txt"), max_task_reworks=1)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
            todo_text = file.read()
        assert "[x] T1" in todo_text and "[x] T2 — Write b.txt" in todo_text


def test_reviewer_reason_survives_an_escalation_that_keeps_the_task():
    backend = model.MockBackend([
        text_response(WRITE_BAD), text_response(REWORK),
        text_response(WRITE_BAD), text_response(REWORK),                                    # exhausted
        manager_response(add=["Write b.txt"], project="CONTINUE"),                          # T1 itself is unchanged
        text_response("WRITE a.txt\nok\nEND WRITE\nDone"), text_response(ACCEPT),
        text_response("WRITE b.txt\nb\nEND WRITE\nDone"), text_response(ACCEPT),
        manager_response(done=[1, 2]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = start(tmp, todo_item(1, "Write a.txt"), max_task_reworks=1, provisional_batch=2)
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        retry = backend.call_history[5]["messages"][-1]["content"]
        assert "Previous reviewer feedback" in retry and "a.txt must say ok" in retry
