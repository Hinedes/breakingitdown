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

    def test_checked_worker_survives_backend_error(self):
        # Use a real init, then run Worker with a backend that crashes after checkbox
        class CrashAfterCheck:
            call_count = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_count += 1
                if self.call_count == 1:
                    return {"role": "assistant", "content": "WRITE docs/work/T1.md\ndata\nEND WRITE\nWRITE docs/todo.md\n- [x] T1 — Test\nEND WRITE\n", "tool_calls": None, "finish_reason": "stop"}
                raise RuntimeError("simulated crash after checkbox")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, worker_timeout=10, inactivity_timeout=10)
            # Init with a normal backend
            init_backend = model.MockBackend([text_response("- [ ] T1 — Test\n")])
            assert harness.init_project("test", cfg, backend=init_backend)["status"] == "success"
            # Run worker with crash backend
            result = harness.run_worker_session(1, cfg, backend=CrashAfterCheck())
            assert result["status"] == "submitted", f"expected submitted, got {result}"
            assert result["termination"] == "error", f"expected error termination, got {result}"
            assert os.path.exists(os.path.join(tmp, "docs/work/T1.md")), "artifact should survive"

    def test_unchecked_worker_crash_rolls_back(self):
        # Worker crashes before checking checkbox → rollback
        class CrashBeforeCheck:
            def run(self, messages, tools, max_tokens=None):
                raise RuntimeError("early crash")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, "docs/worker.md"), "w") as f: f.write("# Worker\n")
            from bid import vc
            vc.VersionControl(tmp).init()
            cfg = {"workspace": tmp, "max_tokens": 256, "worker_timeout": 5, "inactivity_timeout": 5, "repeat_action_limit": 3}
            result = harness.run_worker_session(1, cfg, backend=CrashBeforeCheck())
            assert result["status"] == "error", f"expected error, got {result}"

    def test_unterminated_write_rejected(self):
        cmds = adapter._parse_content_into_turns("WRITE x.txt\ncontent\nDone")
        assert len(cmds) == 1
        assert cmds[0]["type"] == "WRITE_UNTERMINATED"

    def test_unterminated_write_does_not_affect_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "test.txt")
            with open(p, "w") as f: f.write("original")
            # Verify the parser doesn't change anything on its own
            cmds = adapter._parse_content_into_turns("WRITE test.txt\nnew content\nDone")
            assert cmds[0]["type"] == "WRITE_UNTERMINATED"
            with open(p) as f:
                assert f.read() == "original"

    def test_repeated_read_not_useful(self):
        """A WorkerAdapter that reads the same file twice should not mark useful."""
        todo = "- [ ] T1 — Test\n"
        responses = [
            text_response("READ docs/todo.md"),
            text_response("READ docs/todo.md"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write(todo)
            with open(os.path.join(tmp, "docs/worker.md"), "w") as f: f.write("# Worker\n")
            from bid import vc
            vc.VersionControl(tmp).init()
            cfg = {"workspace": tmp, "max_tokens": 256, "worker_timeout": 10,
                   "inactivity_timeout": 10, "repeat_action_limit": 3}
            r = harness.run_worker_session(1, cfg, backend=backend)
            # Should stall (checked or rolled back)
            assert r["status"] == "error"  # unchecked = error

    def test_plan_validates_normalized_dup_outputs(self):
        bad = "- [ ] T1 — A\n  Output: docs/work/T1.md\n- [ ] T2 — B\n  Output: docs/work/../work/T1.md\n"
        tasks = todo.parse_todo(bad)
        valid, reason = adapter.validate_todo_tasks(tasks)
        assert not valid, f"should reject normalized dup: {reason}"

    def test_plan_rejects_empty_output(self):
        bad = "- [ ] T1 — A\n  Output:\n"
        tasks = todo.parse_todo(bad)
        valid, reason = adapter.validate_todo_tasks(tasks)
        assert not valid, f"should reject empty Output: {reason}"

    def test_plan_rejects_docs_reviews_path(self):
        bad = "- [ ] T1 — A\n  Output: docs/reviews/T1.md\n"
        tasks = todo.parse_todo(bad)
        valid, reason = adapter.validate_todo_tasks(tasks)
        assert not valid, f"should reject docs/reviews path: {reason}"

    def test_stale_done_invalidated_by_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write("- [x] T1 — Test\n")
            with open(os.path.join(tmp, "docs/task.md"), "w") as f: f.write("# Task\n\nTest\n")
            with open(os.path.join(tmp, "docs/project-status.md"), "w") as f: f.write("# Project Status\n\nDONE\n")
            with open(os.path.join(tmp, "docs/decisions.md"), "w") as f: f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            os.makedirs(os.path.join(tmp, "docs/work"))
            with open(os.path.join(tmp, "docs/work/T1.md"), "w") as f: f.write("correct")
            tasks = todo.parse_todo("- [x] T1 — Test\n")
            correct_hash = harness._compute_plan_hash(tmp, tasks)
            hash_path = os.path.join(tmp, "docs/.completed_hash")
            with open(hash_path, "w") as f: f.write(correct_hash)
            # Hash matches → completed
            assert harness._is_completed(tmp, tasks)
            # Mutate TODO → hash no longer matches
            with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write("- [x] T1 — Changed\n")
            assert not harness._is_completed(tmp, tasks), "stale DONE should be invalid after TODO change"

    def test_path_blocked_covers_all_control_paths(self):
        assert permissions._path_blocked("docs/reviews")
        assert permissions._path_blocked("docs/reviews/T1.md")
        assert permissions._path_blocked("docs/.completed_hash")
        assert permissions._path_blocked(".bid")
        assert permissions._path_blocked(".bid/states/s0")
        assert not permissions._path_blocked("docs/work/T1.md")
        assert not permissions._path_blocked("docs/todo.md")

    def test_rejects_T01_task_id(self):
        assert not adapter.validate_todo_tasks(todo.parse_todo("- [ ] T01 — A\n"))[0]
        assert adapter.validate_todo_tasks(todo.parse_todo("- [ ] T1 — A\n"))[0]

    def test_rejects_normalized_dup_output(self):
        bad = "- [ ] T1 — A\n  Output: docs/work/T1.md\n- [ ] T2 — B\n  Output: docs/work/../work/T1.md\n"
        assert not adapter.validate_todo_tasks(todo.parse_todo(bad))[0]

    def test_persisted_completion_resume(self):
        """After completion, a second process sees DONE and returns done."""
        todo_initial = "- [ ] T1 — First\n- [ ] T2 — Second\n"
        responses = [
            text_response(todo_initial),
            text_response("WRITE docs/work/T1.md\none\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First\n- [ ] T2 — Second\nEND WRITE\n\nDone"),
            text_response("WRITE docs/work/T2.md\ntwo\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First\n- [x] T2 — Second\nEND WRITE\n\nDone"),
            text_response("ACCEPT\nReason: Good."),
            text_response("ACCEPT\nReason: Good."),
            text_response("COMPLETE\nReason: All done."),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("test", cfg, backend=backend)["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            # Now simulate a process restart: create new backend, re-run
            backend2 = model.MockBackend([])
            result2 = harness.run_project(cfg, backend=backend2)
            assert result2["status"] == "done", f"resume should return done: {result2}"


class TestSearch:
    def test_parse_search_command(self):
        cmds = adapter._parse_content_into_turns("SEARCH Python testing\nDone")
        assert len(cmds) == 2
        assert cmds[0]["type"] == "SEARCH"
        assert cmds[0]["query"] == "Python testing"

    def test_cache_identical_queries(self):
        from bid.search import SearchCache, execute_search, MockSearchProvider, SearchResult
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            cache = SearchCache(tmp)
            provider = MockSearchProvider({
                "test query": [SearchResult("Result", "http://x.com", "Test summary", "Test extract")]
            })
            p1, n1, _, _ = execute_search(tmp, 1, "test query", cache, provider)
            assert n1 == 1
            assert cache.get("test query") is not None
            p2, n2, _, is_cache = execute_search(tmp, 1, "test query", cache, provider)
            assert is_cache
            assert p2 == p1, "cache hit should return same path"

    def test_search_in_worker_adapter(self):
        from bid.search import MockSearchProvider, SearchResult
        todo_initial = "- [ ] T1 — Research topic\n"
        responses = [
            text_response(todo_initial),
            text_response("SEARCH current Python version\n"),
            text_response("READ docs/research/T1/search-001.md"),
            text_response("WRITE docs/work/T1.md\nPython 3.12\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — Research topic\nEND WRITE\n\nDone"),
        ]
        backend = model.MockBackend(responses)
        from bid.search import MockSearchProvider, SearchResult
        provider = MockSearchProvider({
            "current python version": [SearchResult("Python 3.12", "https://python.org", "Python 3.12 released", "Python 3.12 features")]
        })
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, worker_timeout=10, inactivity_timeout=10)
            assert harness.init_project("test", cfg, backend=backend)["status"] == "success"
            # Run worker with search provider
            from bid.adapter import WorkerAdapter
            adapter = WorkerAdapter(cfg, 1, search_provider=provider)
            result = adapter.run(backend)
            assert result["status"] == "done", f"worker should complete: {result}"
            # Verify search artifact was created
            research_file = os.path.join(tmp, "docs", "research", "T1", "search-001.md")
            assert os.path.exists(research_file), f"search result should exist: {research_file}"
            with open(research_file) as f:
                content = f.read()
                assert "Python 3.12" in content
            # Verify work artifact was created
            work_file = os.path.join(tmp, "docs/work/T1.md")
            assert os.path.exists(work_file)
            with open(work_file) as f:
                assert "Python 3.12" in f.read()

    def test_search_limit_enforced(self):
        from bid.search import MockSearchProvider, SearchResult
        provider = MockSearchProvider()
        cfg_limited = {"max_searches_per_worker": 2}
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs/todo.md"), "w") as f: f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, "docs/worker.md"), "w") as f: f.write("# Worker\n")
            from bid import vc
            vc.VersionControl(tmp).init()
            from bid.adapter import WorkerAdapter
            cfg = {"workspace": tmp, "max_tokens": 256, "worker_timeout": 5, "inactivity_timeout": 5,
                   "repeat_action_limit": 10, "max_searches_per_worker": 2}
            responses = [
                text_response("SEARCH query 1"),
                text_response("SEARCH query 2"),
                text_response("SEARCH query 3"),
            ]
            backend = model.MockBackend(responses)
            adapter = WorkerAdapter(cfg, 1, search_provider=provider)
            result = adapter.run(backend)
            # Should return with stalled status (searches 1 and 2 succeed, 3 hits limit)
            assert result["status"] in ("stalled", "timeout")

    def test_worker_cannot_write_research(self):
        from bid import permissions
        assert not permissions.check_write_permission("docs/research/T1/search-001.md", permissions.ROLE_WORKER, 1)[0]
        assert not permissions.check_write_permission("docs/research", permissions.ROLE_WORKER, 1)[0]

    def test_worker_can_read_research(self):
        from bid import permissions
        ok, _ = permissions.check_read_permission("docs/research/T1/search-001.md", permissions.ROLE_WORKER)
        assert ok

    def test_cache_survives_new_adapter(self):
        """Persistent cache should survive a new WorkerAdapter instance."""
        from bid.search import SearchCache, MockSearchProvider, SearchResult, execute_search
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            cache = SearchCache(tmp)
            provider = MockSearchProvider({"python version": [SearchResult("Py3", "https://py.org", "Python 3")]})
            p1, n1, _, _ = execute_search(tmp, 1, "python version", cache, provider)
            assert n1 == 1
            cache2 = SearchCache(tmp)
            cached = cache2.get("python version")
            assert cached is not None, "cache should persist across instances"

    def test_url_validation_rejects_non_http(self):
        from bid.search import SearchResult
        r = SearchResult("Test", "ftp://bad.com", "summary")
        assert r.url == "", f"non-http URL should be rejected: {r.url}"
        r2 = SearchResult("Test", "https://good.com", "summary")
        assert r2.url == "https://good.com"

    def test_search_field_bounds(self):
        from bid.search import SearchResult
        long_title = "x" * 500
        r = SearchResult(long_title, "https://example.com", "summary")
        assert len(r.title) <= 200, f"title should be bounded: {len(r.title)}"

    def test_mock_provider_not_default(self):
        """Default provider should not be MockSearchProvider."""
        from bid import search as search_mod
        import tempfile
        cfg = {}
        provider = search_mod.create_provider(cfg)
        assert not isinstance(provider, search_mod.MockSearchProvider), "mock should not be default"
