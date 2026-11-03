"""Tests for places where the audit-hardened line and the repo-context line meet."""
import os
import tempfile

from bid import adapter, harness, model, vc
from tests.test_repo_context import prepare_worker, worker_config
from tests.test_worker_continuity import (
    config,
    manager_response,
    prepare_workspace,
    text_response,
    todo_item,
)


def test_reviewer_prompt_is_project_neutral():
    with tempfile.TemporaryDirectory() as tmp:
        prepare_workspace(tmp, todo_item(1, "Do it"))
        store = vc.VersionControl(tmp)
        base = store.get_current()
        candidate = store.save_state("worker", "T1")
        backend = model.MockBackend([text_response("ACCEPT\nReason: ok")])
        adapter.TaskReviewAdapter(config(tmp), 1, base_state=base, candidate_state=candidate).run(backend)
        prompt = backend.call_history[0]["messages"][1]["content"].lower()
        assert "argus" not in prompt
        assert "physicallyinvalid" not in prompt
        assert "beam cone" not in prompt
        assert "no unrelated refactors" in prompt


def test_find_closure_text_does_not_promise_commands_that_may_be_off():
    # RUN is denied by default and SEARCH can be disabled, so the closure text must not name them.
    for text in (
        adapter._FIND_CLOSURE_STATEMENT,
        adapter._FIND_CLOSED_RESULT,
        adapter._EXECUTION_PHASE_INSTRUCTION,
    ):
        assert "RUN" not in text and "SEARCH" not in text
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        cfg = worker_config(workspace, "tools", repo_context_find_closure_max_chars=120)
        packet, _ = adapter.WorkerAdapter(cfg, 1)._build_find_closure_packet("fingerprint")
        assert "RUN" not in packet and "SEARCH" not in packet


def test_continuous_mode_drops_carried_history_after_manager_rework():
    init_todo = todo_item(1, "First") + todo_item(2, "Second")
    backend = model.MockBackend([
        text_response("WRITE a.txt\nA\nEND WRITE\nDone"),
        text_response("ACCEPT\nReason: ok"),
        text_response("WRITE b.txt\nB\nEND WRITE\nDone"),
        text_response("ACCEPT\nReason: ok"),
        manager_response(rework=[1, "redo the first task"], project="CONTINUE"),
        text_response("WRITE a.txt\nA2\nEND WRITE\nDone"),
        text_response("ACCEPT\nReason: ok"),
        text_response("WRITE b.txt\nB\nEND WRITE\nDone"),
        text_response("ACCEPT\nReason: ok"),
        manager_response(done=[1, 2]),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config(tmp, worker_continuity="continuous", provisional_batch=2)
        assert harness.init_project("two tasks", cfg, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
        assert harness.run_project(cfg, backend=backend)["status"] == "done"
        t1_calls = [
            c for c in backend.call_history
            if not c["messages"][0]["content"].startswith("You are BID")
            and "Task T1:" in c["messages"][-1]["content"]
        ]
        assert len(t1_calls) == 2
        # The T1 redo starts fresh: the carried conversation belonged to workspace state that was rolled back.
        assert len(t1_calls[1]["messages"]) == 2
        assert "Task T2:" not in " ".join(m["content"] for m in t1_calls[1]["messages"])
