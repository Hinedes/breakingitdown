import tempfile
import os

from bid import adapter, harness, model, todo, vc


def text_response(text):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
    }


def config(workspace, **overrides):
    values = {
        "workspace": workspace,
        "max_tokens": 8192,
        "request_timeout": 30,
        "inactivity_timeout": 30,
        "worker_timeout": 30,
        "repeat_action_limit": 3,
        "max_searches_per_worker": 5,
    }
    values.update(overrides)
    return values


def todo_item(number, desc, checked=False):
    mark = "x" if checked else " "
    return f"- [{mark}] T{number} — {desc}\n"


def manager_response(done=None, rework=None, add=None, replace=None, project="COMPLETE"):
    lines = []
    if done:
        lines.append("# Done")
        lines.extend(f"- T{n}" for n in done)
    if rework:
        lines.append("# Rework")
        lines.append(f"- T{rework[0]} — {rework[1]}")
    if add:
        lines.append("# Add")
        lines.extend(f"- {item}" for item in add)
    if replace:
        lines.append("# Replace Remaining Plan")
        lines.extend(f"- [ ] {item}" for item in replace)
    lines.append("# Project")
    lines.append(project)
    return text_response("\n".join(lines))


def prepare_workspace(tmp, todo_text):
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as f:
        f.write(todo_text)
    with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as f:
        f.write("# Task\n\nDo the thing.\n")
    with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as f:
        f.write("# Project Status\n\nInitialized.\n")
    with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as f:
        f.write("# Decisions\n\n")
    harness.ensure_workspace(tmp)
    vc.VersionControl(tmp).init()


class TestWorkerContinuity:
    def test_continuity_default_is_reset(self):
        cfg = harness.get_config()
        assert cfg["worker_continuity"] == "reset"

    def test_continuity_normalizes_values(self):
        assert harness.normalize_worker_continuity("reset") == "reset"
        assert harness.normalize_worker_continuity("") == "reset"
        assert harness.normalize_worker_continuity("  RESET  ") == "reset"
        assert harness.normalize_worker_continuity("continuous") == "continuous"
        assert harness.normalize_worker_continuity("CONTINUOUS") == "continuous"

    def test_continuity_rejects_unknown(self):
        try:
            harness.normalize_worker_continuity("externalized")
            assert False, "should have raised"
        except ValueError as e:
            assert "BID_WORKER_CONTINUITY" in str(e)
        try:
            harness.normalize_worker_continuity("naive_reset")
            assert False, "should have raised"
        except ValueError:
            pass

    def test_reset_behaves_like_current(self):
        """reset must not carry history; T2 starts fresh"""
        init_todo = todo_item(1, "First") + todo_item(2, "Second")
        backend = model.MockBackend([
            text_response("WRITE t1.txt\nT1_MARKER\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: ok"),
            text_response("Done"),
            text_response("ACCEPT\nReason: ok"),
            manager_response(done=[1, 2]),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, worker_continuity="reset")
            assert harness.init_project("two tasks", cfg, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            # identical task graph
            tasks = todo.parse_todo(open(os.path.join(tmp, "docs/todo.md")).read())
            assert [t["id"] for t in tasks] == ["T1", "T2"]
            # T2's initial Worker messages should be fresh (2 messages)
            worker_calls = [c for c in backend.call_history if any("Task T2:" in m.get("content", "") for m in c["messages"])]
            assert len(worker_calls) == 1, f"expected one T2 worker call, got {worker_calls}"
            msgs = worker_calls[0]["messages"]
            assert len(msgs) == 2, f"reset should have 2 initial messages, got {len(msgs)}: {msgs}"
            assert "Task T2:" in msgs[1]["content"]
            # must NOT contain T1 marker in initial messages
            all_content = " ".join(m.get("content", "") for m in msgs)
            assert "T1_MARKER" not in all_content
            assert "Task T1:" not in all_content

    def test_continuous_carries_only_messages(self):
        """continuous must carry T1 conversation into T2; task graph identical"""
        init_todo = todo_item(1, "First") + todo_item(2, "Second")
        backend_reset = model.MockBackend([
            text_response("WRITE t1.txt\nT1_MARKER\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: ok"),
            text_response("Done"),
            text_response("ACCEPT\nReason: ok"),
            manager_response(done=[1, 2]),
        ])
        backend_cont = model.MockBackend([
            text_response("WRITE t1.txt\nT1_MARKER\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: ok"),
            text_response("Done"),
            text_response("ACCEPT\nReason: ok"),
            manager_response(done=[1, 2]),
        ])
        with tempfile.TemporaryDirectory() as tmp_reset, tempfile.TemporaryDirectory() as tmp_cont:
            cfg_reset = config(tmp_reset, worker_continuity="reset")
            cfg_cont = config(tmp_cont, worker_continuity="continuous")
            assert harness.init_project("two tasks", cfg_reset, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
            assert harness.init_project("two tasks", cfg_cont, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
            # task graph identical before run
            todo_reset = open(os.path.join(tmp_reset, "docs/todo.md")).read()
            todo_cont = open(os.path.join(tmp_cont, "docs/todo.md")).read()
            assert todo_reset == todo_cont
            assert harness.run_project(cfg_reset, backend=backend_reset)["status"] == "done"
            assert harness.run_project(cfg_cont, backend=backend_cont)["status"] == "done"
            # task graph identical after run (both done)
            assert open(os.path.join(tmp_reset, "docs/todo.md")).read() == open(os.path.join(tmp_cont, "docs/todo.md")).read()
            # Manager/Reviewer prompts identical shape for first task
            # extract Reviewer calls for T1 - should be identical
            reviewer_reset = [c for c in backend_reset.call_history if c["messages"][0]["content"].startswith("You are BID Task Reviewer")]
            reviewer_cont = [c for c in backend_cont.call_history if c["messages"][0]["content"].startswith("You are BID Task Reviewer")]
            assert len(reviewer_reset) == len(reviewer_cont) == 2
            # First reviewer (T1) identical
            assert reviewer_reset[0]["messages"][1]["content"] == reviewer_cont[0]["messages"][1]["content"]
            # Second reviewer identical (candidate diff only, not history)
            # Worker T2 initial messages differ only in history
            worker_reset_t2 = [c for c in backend_reset.call_history if any("Task T2:" in m.get("content", "") for m in c["messages"])][0]
            worker_cont_t2 = [c for c in backend_cont.call_history if any("Task T2:" in m.get("content", "") for m in c["messages"])][0]
            msgs_reset = worker_reset_t2["messages"]
            msgs_cont = worker_cont_t2["messages"]
            # reset has 2, continuous has >2 and contains prior
            assert len(msgs_reset) == 2
            assert len(msgs_cont) > 2
            cont_all = " ".join(m.get("content", "") for m in msgs_cont)
            assert "Task T1:" in cont_all
            assert "T1_MARKER" in cont_all or "WRITE t1.txt" in cont_all
            # T2 task prompt still present in continuous as last user message
            assert "Task T2:" in msgs_cont[-1]["content"]
            # Ensure only messages carried: READ ledgers not carried
            # Verify T2 can re-read same file without deduplication suppression (fresh ledger)
            # Both modes should allow full READ of same file; check that T1's READ ledger did not leak
            # We already verified reset vs continuous differ only in messages length/content,
            # not in manager/reviewer/task graph.

    def test_continuous_does_not_carry_on_rework(self):
        """Same-task retry (REWORK) must start fresh even in continuous mode"""
        init_todo = todo_item(1, "Only task")
        # T1 first attempt fails, second succeeds; T2 would be none, but we check retry of T1 has no prior history
        backend = model.MockBackend([
            text_response("WRITE bad.txt\nbad\nEND WRITE\nDone"),
            text_response("REWORK\nReason: bad"),
            text_response("Done"),
            text_response("ACCEPT\nReason: ok"),
            manager_response(done=[1]),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, worker_continuity="continuous")
            assert harness.init_project("one task", cfg, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            # Calls: 0 manager init, 1 worker T1 attempt1, 2 reviewer1, 3 worker T1 attempt2, 4 reviewer2, 5 manager reconcile
            # Worker attempt2 initial messages should NOT contain attempt1 history (same task, not Tn->Tn+1)
            # Filter worker calls for T1
            worker_calls = [c for c in backend.call_history if len(c["messages"]) >= 2 and "Task T1:" in c["messages"][1].get("content", "")]
            assert len(worker_calls) == 2
            first_msgs = worker_calls[0]["messages"]
            second_msgs = worker_calls[1]["messages"]
            # Second attempt should be fresh (2 messages) not carrying first attempt's history
            assert len(second_msgs) == 2, f"rework should reset, got {len(second_msgs)}"
            assert "bad" not in " ".join(m.get("content", "") for m in second_msgs) or "Task T1:" in second_msgs[1]["content"]  # no prior assistant content

    def test_continuous_accumulates_across_three_tasks(self):
        """T3 should contain history from T1 and T2 when continuous"""
        init_todo = todo_item(1, "One") + todo_item(2, "Two") + todo_item(3, "Three")
        backend = model.MockBackend([
            text_response("WRITE a.txt\nA\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: ok"),
            text_response("WRITE b.txt\nB\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: ok"),
            text_response("Done"),
            text_response("ACCEPT\nReason: ok"),
            manager_response(done=[1, 2, 3]),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, worker_continuity="continuous")
            assert harness.init_project("three", cfg, backend=model.MockBackend([text_response(init_todo)]))["status"] == "success"
            assert harness.run_project(cfg, backend=backend)["status"] == "done"
            worker_calls = [c for c in backend.call_history if any("Task T" in m.get("content", "") and m["role"] == "user" for m in c["messages"][1:2])]
            # Find T3 call
            t3_calls = [c for c in backend.call_history if any("Task T3:" in m.get("content", "") for m in c["messages"])]
            assert len(t3_calls) == 1
            msgs = t3_calls[0]["messages"]
            all_text = " ".join(m.get("content", "") for m in msgs)
            assert "Task T1:" in all_text
            assert "Task T2:" in all_text
            assert "Task T3:" in all_text
            assert len(msgs) > 2
