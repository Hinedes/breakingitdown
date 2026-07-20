import json
import os
import tempfile

from bid import harness, model, permissions, session, todo, tools, vc
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
        todo_t1 = "- [x] T1 — First artifact\n- [ ] T2 — Second artifact\n"
        todo_all = "- [x] T1 — First artifact\n- [x] T2 — Second artifact\n"
        responses = [
            text_response(todo_initial),
            text_response("WRITE output/t1.md\none\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First artifact\n- [ ] T2 — Second artifact\nEND WRITE\n\nDone"),
            text_response("WRITE output/t2.md\ntwo\nEND WRITE"),
            text_response("WRITE docs/todo.md\n- [x] T1 — First artifact\n- [x] T2 — Second artifact\nEND WRITE\n\nDone"),
            tool_response("read_file", {"path": "output/t1.md"}),
            tool_response("read_file", {"path": "output/t2.md"}),
            tool_response("write_file", {"path": "docs/project-status.md", "content": "# Project Status\n\nDONE\n"}),
            text_response("Done"),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            initialized = harness.init_project("Produce two artifacts", cfg, backend=backend)
            assert initialized["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            assert os.path.exists(os.path.join(tmp, "output", "t1.md"))
            assert os.path.exists(os.path.join(tmp, "output", "t2.md"))
            states = vc.VersionControl(tmp)._list_states()
            assert states == ["s0", "s1", "s2", "s3", "s4"]
            assert all(call["max_tokens"] == 8192 for call in backend.call_history)
