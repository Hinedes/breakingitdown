import json
import os
import tempfile

from bid import adapter, harness, model, permissions, session, todo, tools, vc
from bid.observer import Observer


def tool_call(name, arguments):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def tool_response(name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [tool_call(name, arguments)],
        "finish_reason": "tool_calls",
    }


def text_response(text):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
    }


def text_tool_response(name, arguments, raw_text=None):
    """Simulate a text-transport tool call as LlamaCppBackend would produce."""
    text = raw_text or json.dumps({"tool_calls": [{"tool": name, "arguments": arguments}]})
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [tool_call(name, arguments)],
        "finish_reason": "tool_calls",
        "tool_transport": "text",
    }


def config(workspace, **overrides):
    values = {
        "workspace": workspace,
        "max_tokens": 8192,
        "request_timeout": 30,
        "inactivity_timeout": 30,
        "worker_timeout": 30,
        "repeat_action_limit": 3,
    }
    values.update(overrides)
    return values


def prepare_worker_workspace(tmp, todo_text="- [ ] T1 — Write result\n"):
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
        file.write(todo_text)
    with open(os.path.join(tmp, "docs", "worker.md"), "w", encoding="utf-8") as file:
        file.write("# Worker\n")
    system = vc.VersionControl(tmp)
    system.init()
    return system


class TestObserver:
    def test_task_transition_and_done_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_worker_workspace(tmp)
            observer = Observer(tmp, 1)
            assert not observer.task_just_became_checked()
            with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
                file.write("- [x] T1 — Write result\n")
            assert observer.task_just_became_checked()
            assert not observer.task_just_became_checked()
            assert observer.seen_done("work\nDone")
            assert not observer.seen_done("Not done yet")

    def test_poll_changes_detects_add_modify_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_worker_workspace(tmp)
            observer = Observer(tmp, 1)
            path = os.path.join(tmp, "result.md")
            with open(path, "w", encoding="utf-8") as file:
                file.write("a")
            assert "result.md" in observer.poll_changes()
            with open(path, "w", encoding="utf-8") as file:
                file.write("b")
            assert "result.md" in observer.poll_changes()
            os.remove(path)
            assert "result.md" in observer.poll_changes()

    def test_repeated_action_count(self):
        observer = Observer("/tmp", 0)
        assert observer.record_action("read_file", {"path": "."}, "docs/") == 1
        assert observer.record_action("read_file", {"path": "."}, "docs/") == 2
        assert observer.record_action("read_file", {"path": "x"}, "x") == 1


class TestSession:
    def test_tool_event_is_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "f.txt"), "w") as f:
                f.write("data")
            backend = model.MockBackend([tool_response("read_file", {"path": "docs/f.txt"})])
            result = session.run_session(
                "system",
                "assignment",
                tools.get_tools_for_role(permissions.ROLE_WORKER, 1),
                backend,
                config(tmp),
                tmp,
                permissions.ROLE_WORKER,
                1,
            )
        assert result["tool_calls"]
        assert result["tool_events"][0]["name"] == "read_file"
        assert result["tool_events"][0]["success"]

    def test_malformed_arguments_are_reported_not_executed(self):
        response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "bad",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{"},
            }],
            "finish_reason": "tool_calls",
        }
        backend = model.MockBackend([response])
        with tempfile.TemporaryDirectory() as tmp:
            result = session.run_session(
                "system", "assignment", tools.get_tools_for_role("worker", 1),
                backend, config(tmp), tmp, "worker", 1,
            )
        assert not result["tool_events"][0]["success"]
        assert "malformed" in result["tool_events"][0]["result"]

    def test_max_tokens_reaches_backend_unchanged(self):
        backend = model.MockBackend([text_response("Done")])
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("system", "assignment", [], backend, config(tmp), tmp, "manager")
        assert backend.call_history[0]["max_tokens"] == 8192


class TestTranscript:
    def _run_two_reads(self, tmp, transport):
        """Helper: run two consecutive read_file tools, return call_history."""
        t1 = transport("read_file", {"path": "f1.txt"})
        t2 = transport("read_file", {"path": "f2.txt"})
        t3 = text_response("Done")
        backend = model.MockBackend([t1, t2, t3])
        with open(os.path.join(tmp, "f1.txt"), "w") as f: f.write("a")
        with open(os.path.join(tmp, "f2.txt"), "w") as f: f.write("b")
        cfg = config(tmp)
        cfg["max_tokens"] = 256
        # Run three turns: read, read, Done
        from bid import session as sess
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "do it"}]
        tools_list = [t for t in tools.get_tools_for_role("worker", 1) if t["name"] == "read_file"]
        for _ in range(3):
            r = sess.run_turn(msgs, tools_list, backend, cfg, tmp, "worker", 1)
            msgs = r["messages"]
        return backend.call_history

    def test_text_transport_never_uses_tool_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self._run_two_reads(tmp, text_tool_response)
            for req in history:
                for msg in req["messages"]:
                    assert msg["role"] != "tool", f"found role=tool in {msg}"
                    if msg["role"] == "assistant":
                        assert "tool_calls" not in msg, f"found tool_calls in assistant msg"

    def test_text_transport_roles_are_system_user_assistant_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self._run_two_reads(tmp, text_tool_response)
            for req in history:
                roles = [m["role"] for m in req["messages"]]
                for r in roles:
                    assert r in ("system", "user", "assistant"), f"unexpected role {r}"

    def test_native_transport_uses_openai_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self._run_two_reads(tmp, tool_response)
            # At least one request should have tool role or tool_calls
            has_tool = any(
                msg.get("tool_calls") or msg["role"] == "tool"
                for req in history for msg in req["messages"]
            )
            assert has_tool, "native transport should include tool role or tool_calls"

    def test_three_ops_reach_fourth_request(self):
        """Three text-tool operations should produce four backend requests (3 ops + Done)."""
        with tempfile.TemporaryDirectory() as tmp:
            t1 = text_tool_response("read_file", {"path": "f1.txt"})
            t2 = text_tool_response("read_file", {"path": "f2.txt"})
            t3 = text_tool_response("write_file", {"path": "f3.txt", "content": "c"})
            t4 = text_response("Done")
            backend = model.MockBackend([t1, t2, t3, t4])
            with open(os.path.join(tmp, "f1.txt"), "w") as f: f.write("a")
            with open(os.path.join(tmp, "f2.txt"), "w") as f: f.write("b")
            cfg = config(tmp)
            cfg["max_tokens"] = 256
            from bid import session as sess
            msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "do it"}]
            tools_list = [t for t in tools.get_tools_for_role("worker", 1) if t["name"] in ("read_file", "write_file")]
            for _ in range(4):
                r = sess.run_turn(msgs, tools_list, backend, cfg, tmp, "worker", 1)
                msgs = r["messages"]
            assert len(backend.call_history) == 4, f"expected 4 requests, got {len(backend.call_history)}"


class TestFileTools:
    def test_repeated_write_overwrites_so_worker_can_backtrack(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker_tools = {tool["name"]: tool for tool in tools.get_tools_for_role("worker", 1)}
            write = worker_tools["write_file"]["handler"]
            assert write({"path": "result.md", "content": "first"}, tmp, "worker", 1).startswith("wrote")
            assert write({"path": "result.md", "content": "corrected"}, tmp, "worker", 1).startswith("wrote")
            with open(os.path.join(tmp, "result.md"), encoding="utf-8") as file:
                assert file.read() == "corrected"

    def test_worker_may_toggle_only_own_checkbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_worker_workspace(tmp, "- [ ] T1 — One\n- [ ] T2 — Two\n")
            write = next(tool for tool in tools.get_tools_for_role("worker", 1) if tool["name"] == "write_file")["handler"]
            accepted = write(
                {"path": "docs/todo.md", "content": "- [x] T1 — One\n- [ ] T2 — Two\n"},
                tmp, "worker", 1,
            )
            assert accepted.startswith("wrote")
            rejected = write(
                {"path": "docs/todo.md", "content": "- [x] T1 — One\n- [x] T2 — Two\n"},
                tmp, "worker", 1,
            )
            assert rejected.startswith("permission denied")

    def test_no_terminal_tools_are_exposed(self):
        names = {tool["name"] for tool in tools.get_tools_for_role("worker", 1)}
        assert "finish" not in names
        assert "check_own_task" not in names
        assert "submit_task" not in names


class TestWorkerLifecycle:
    def test_checked_worker_may_revise_then_done(self):
        responses = [
            text_response("WRITE result.md\ndraft\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — Write result\nEND WRITE"),
            text_response("WRITE result.md\nfinal\nEND WRITE\n\nDone"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            prepare_worker_workspace(tmp)
            result = harness.run_worker_session(1, config(tmp), backend=backend)
            assert result["status"] == "submitted"
            assert result["termination"] == "normal"
            with open(os.path.join(tmp, "result.md"), encoding="utf-8") as file:
                assert file.read() == "final"

    def test_done_without_checkbox_does_not_terminate_worker(self):
        responses = [
            text_response("Done"),
            text_response("WRITE docs/todo.md\n- [x] T1 — Write result\nEND WRITE"),
            text_response("Done"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            prepare_worker_workspace(tmp)
            result = harness.run_worker_session(1, config(tmp), backend=backend)
            assert result["status"] == "submitted"
            assert len(backend.call_history) == 3
            assert any("still unchecked" in message.get("content", "") for message in backend.call_history[1]["messages"])

    def test_unchecked_stalled_worker_rolls_back(self):
        responses = [
            text_response("WRITE leak.md\nunfinished\nEND WRITE"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            system = prepare_worker_workspace(tmp)
            result = harness.run_worker_session(1, config(tmp), backend=backend)
            assert result["status"] == "error"
            assert not os.path.exists(os.path.join(tmp, "leak.md"))
            assert system.get_current() == "s0"
            assert system._list_states() == ["s0"]

    def test_checked_stalled_worker_is_saved_as_abnormal_submission(self):
        responses = [
            text_response("WRITE docs/todo.md\n- [x] T1 — Write result\nEND WRITE"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
            text_response("READ nonexistent.md"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            system = prepare_worker_workspace(tmp)
            cfg = config(tmp)
            cfg["repeat_action_limit"] = 3
            result = harness.run_worker_session(1, cfg, backend=backend)
            assert result["status"] == "submitted"
            assert result["termination"] == "stalled"
            assert system.get_current() == "s1"


class TestFullFlow:
    def test_manager_workers_manager_done(self):
        todo_initial = "- [ ] T1 — First artifact\n- [ ] T2 — Second artifact\n"
        responses = [
            text_response(todo_initial),
            text_response("WRITE docs/work/T1.md\none\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First artifact\n- [ ] T2 — Second artifact\nEND WRITE\n\nDone"),
            text_response("WRITE docs/work/T2.md\ntwo\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First artifact\n- [x] T2 — Second artifact\nEND WRITE\n\nDone"),
            text_response("ACCEPT\nReason: Good."),
            text_response("ACCEPT\nReason: Good."),
            text_response("COMPLETE\nReason: All done."),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            initialized = harness.init_project("Produce two artifacts", cfg, backend=backend)
            assert initialized["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            assert os.path.exists(os.path.join(tmp, "docs", "work", "T1.md"))
            assert os.path.exists(os.path.join(tmp, "docs", "work", "T2.md"))
            states = vc.VersionControl(tmp)._list_states()
            assert states == ["s0", "s1", "s2", "s3", "s4"]
            assert all(call["max_tokens"] == 8192 for call in backend.call_history)


class TestArtifactReview:
    def _prepare(self, tmp, task_desc, artifact_content, todo_text=None):
        os.makedirs(os.path.join(tmp, "docs", "work"), exist_ok=True)
        with open(os.path.join(tmp, "docs/todo.md"), "w", encoding="utf-8") as f:
            f.write(todo_text or f"- [x] T1 — {task_desc}\n")
        with open(os.path.join(tmp, "docs/task.md"), "w", encoding="utf-8") as f:
            f.write(f"# Task\n\n{task_desc}\n")
        with open(os.path.join(tmp, "docs/project-status.md"), "w", encoding="utf-8") as f:
            f.write("# Project Status\n\nInitialized.\n")
        with open(os.path.join(tmp, "docs/decisions.md"), "w", encoding="utf-8") as f:
            f.write("# Decisions\n\n")
        harness.ensure_workspace(tmp)
        with open(os.path.join(tmp, "docs/work/T1.md"), "w", encoding="utf-8") as f:
            f.write(artifact_content)

    def test_accepts_good_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Write a poem.", "Roses are red.\nViolets are blue.\n")
            backend = model.MockBackend([text_response("ACCEPT\nReason: Good poem.")])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(backend)
            assert r["verdict"] == "ACCEPT"
            review_file = os.path.join(tmp, "docs/reviews/T1.md")
            assert os.path.exists(review_file)
            with open(review_file) as f:
                assert "ACCEPT" in f.read()

    def test_rejects_weak_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Write a 500-word essay.", "Short.")
            backend = model.MockBackend([text_response("REWORK\nReason: Too short.")])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(backend)
            assert r["verdict"] == "REWORK"

    def test_rejects_missing_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Do something.", "")
            os.remove(os.path.join(tmp, "docs/work/T1.md"))
            backend = model.MockBackend([])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(backend)
            assert r["verdict"] == "REWORK"
            assert "not exist" in r["reason"]

    def test_accepts_empty_artifact_with_proper_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Write nothing.", "")
            backend = model.MockBackend([])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(backend)
            assert r["verdict"] == "REWORK"
            assert "empty" in r["reason"]

    def test_retries_on_malformed_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Write code.", "def foo(): pass")
            backend = model.MockBackend([
                text_response("I'm not sure."),
                text_response("ACCEPT\nReason: Works."),
            ])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(backend)
            assert r["verdict"] == "ACCEPT"
            assert len(backend.call_history) == 2


class TestCompletionReview:
    def _prepare(self, tmp, task, todo_text, artifacts):
        os.makedirs(os.path.join(tmp, "docs", "work"), exist_ok=True)
        with open(os.path.join(tmp, "docs/todo.md"), "w", encoding="utf-8") as f:
            f.write(todo_text)
        with open(os.path.join(tmp, "docs/task.md"), "w", encoding="utf-8") as f:
            f.write(f"# Task\n\n{task}\n")
        with open(os.path.join(tmp, "docs/project-status.md"), "w", encoding="utf-8") as f:
            f.write("# Project Status\n\nInitialized.\n")
        with open(os.path.join(tmp, "docs/decisions.md"), "w", encoding="utf-8") as f:
            f.write("# Decisions\n\n")
        harness.ensure_workspace(tmp)
        for path, content in artifacts.items():
            full = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)

    def test_complete_when_all_done(self):
        todo = "- [x] T1 — Write code\n- [x] T2 — Write tests\n"
        artifacts = {"docs/work/T1.md": "def foo(): pass", "docs/work/T2.md": "def test_foo(): pass"}
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Build a library.", todo, artifacts)
            backend = model.MockBackend([text_response("COMPLETE\nReason: All done.")])
            r = adapter.CompletionReviewAdapter(config(tmp)).run(backend)
            assert r["verdict"] == "COMPLETE"

    def test_missing_when_incomplete(self):
        todo = "- [x] T1 — Write code\n"
        artifacts = {"docs/work/T1.md": "def foo(): pass"}
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Build a library with tests and docs.", todo, artifacts)
            backend = model.MockBackend([text_response("MISSING\n- Write tests\n- Write documentation")])
            r = adapter.CompletionReviewAdapter(config(tmp)).run(backend)
            assert r["verdict"] == "MISSING"
            assert "Write tests" in r["missing"]
            assert "Write documentation" in r["missing"]

    def test_retries_on_malformed(self):
        todo = "- [x] T1 — Write code\n"
        artifacts = {"docs/work/T1.md": "def foo(): pass"}
        with tempfile.TemporaryDirectory() as tmp:
            self._prepare(tmp, "Build a library.", todo, artifacts)
            backend = model.MockBackend([
                text_response("Not sure."),
                text_response("COMPLETE\nReason: Done."),
            ])
            r = adapter.CompletionReviewAdapter(config(tmp)).run(backend)
            assert r["verdict"] == "COMPLETE"
            assert len(backend.call_history) == 2


class TestRegression:
    def _ws(self, tmp, todo_text, artifacts):
        os.makedirs(os.path.join(tmp, "docs", "work"), exist_ok=True)
        with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write(todo_text)
        with open(os.path.join(tmp, "docs/task.md"), "w") as f: f.write("# Task\n\nDo it.\n")
        with open(os.path.join(tmp, "docs/project-status.md"), "w") as f: f.write("# Project Status\n\nInit.\n")
        with open(os.path.join(tmp, "docs/decisions.md"), "w") as f: f.write("# Decisions\n\n")
        harness.ensure_workspace(tmp)
        for p, c in artifacts.items():
            fp = os.path.join(tmp, p)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as f: f.write(c)

    def test_review_result_includes_task_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp, "- [x] T1 — A\n", {"docs/work/T1.md": "ok"})
            b = model.MockBackend([text_response("ACCEPT\nReason: Fine.")])
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(b)
            assert r.get("task_number") == 1

    def test_missing_empty_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp, "- [x] T1 — A\n", {"docs/work/T1.md": "ok"})
            b = model.MockBackend([text_response("MISSING")])
            r = adapter.CompletionReviewAdapter(config(tmp)).run(b)
            assert r["verdict"] != "MISSING"

    def test_init_rejects_duplicates(self):
        assert not adapter.validate_todo_tasks(todo.parse_todo("- [ ] T1 — A\n- [ ] T1 — B\n"))[0]

    def test_init_rejects_gaps(self):
        assert not adapter.validate_todo_tasks(todo.parse_todo("- [ ] T1 — A\n- [ ] T3 — C\n"))[0]

    def test_init_rejects_prechecked(self):
        assert not adapter.validate_todo_tasks(todo.parse_todo("- [x] T1 — A\n"))[0]

    def test_init_rejects_empty_desc(self):
        assert not adapter.validate_todo_tasks(todo.parse_todo("- [ ] T1 — \n"))[0]

    def test_init_accepts_valid(self):
        assert adapter.validate_todo_tasks(todo.parse_todo("- [ ] T1 — A\n- [ ] T2 — B\n"))[0]

    def test_literal_backslash_n_preserved(self):
        cmds = adapter._parse_content_into_turns('WRITE x.txt\nline1\\nline2\nEND WRITE')
        assert "\\n" in cmds[0]["content"]

    def test_review_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp, "- [x] T1 — A\n", {"docs/work/T1.md": "good"})
            b = model.MockBackend([text_response("ACCEPT\nReason: Fine.")])
            adapter.ArtifactReviewAdapter(config(tmp), 1).run(b)
            os.remove(os.path.join(tmp, "docs/work/T1.md"))
            r = adapter.ArtifactReviewAdapter(config(tmp), 1).run(b)
            with open(os.path.join(tmp, "docs/reviews/T1.md")) as f:
                assert "REWORK" in f.read()

    def test_full_repair_cycle(self):
        todo_initial = "- [ ] T1 — First\n- [ ] T2 — Second\n"
        responses = [
            text_response(todo_initial),
            text_response("WRITE docs/work/T1.md\nweak\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First\n- [ ] T2 — Second\nEND WRITE\n\nDone"),
            text_response("WRITE docs/work/T2.md\ngood\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First\n- [x] T2 — Second\nEND WRITE\n\nDone"),
            text_response("REWORK\nReason: Weak."),
            text_response("ACCEPT\nReason: Good."),
            text_response("WRITE docs/work/T1.md\nsubstantial\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First\n- [x] T2 — Second\nEND WRITE\n\nDone"),
            text_response("ACCEPT\nReason: Better."),
            text_response("ACCEPT\nReason: Good."),
            text_response("COMPLETE\nReason: Done."),
        ]
        b = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("test", cfg, backend=b)["status"] == "success"
            result = harness.run_project(cfg, backend=b)
            assert result["status"] == "done"
            assert os.path.exists(os.path.join(tmp, "docs/work/T1.md"))
            assert os.path.exists(os.path.join(tmp, "docs/work/T2.md"))
