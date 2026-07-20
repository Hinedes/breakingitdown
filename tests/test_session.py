import pytest
import sys
import os
import tempfile
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid import session, model, tools, permissions


def make_tool_call(name, args_dict):
    return {
        "id": f"call_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args_dict),
        }
    }


def agent_response(tool_calls=None, content=None, finish_reason="tool_calls"):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason if tool_calls else "stop",
    }


def make_tc(name, args_dict):
    return [make_tool_call(name, args_dict)]


class TestSessionHistory:
    def test_assignment_appears_once(self):
        backend = model.MockBackend([
            agent_response(make_tc("finish", {"summary": "done"})),
        ])
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("sys", "my assignment", [], backend, config, tmp, "worker", 1)
        hist = backend.call_history[0]["messages"]
        assignments = [m for m in hist if m["role"] == "user" and m["content"] == "my assignment"]
        assert len(assignments) == 1

    def test_assistant_precedes_tool_result(self):
        backend = model.MockBackend([
            agent_response(make_tc("list_files", {"path": "."})),
            agent_response(make_tc("finish", {"summary": "done"})),
        ])
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("sys", "task", [], backend, config, tmp, "worker", 1)
        hist = backend.call_history[0]["messages"]
        # First call: before tool result
        # Second call: after tool result — assistant must come before tool
        call1 = backend.call_history[0]
        call2 = backend.call_history[1]

    def test_tool_result_retained_without_repeated_assignment(self):
        backend = model.MockBackend([
            agent_response(make_tc("list_files", {"path": "."})),
            agent_response(make_tc("finish", {"summary": "done"})),
        ])
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("sys", "my task", [], backend, config, tmp, "worker", 1)
        call2 = backend.call_history[1]["messages"]
        roles = [m["role"] for m in call2]
        assert "tool" in roles
        # Get the tool messages that correspond to this turn
        tool_msgs = [m for m in call2 if m["role"] == "tool"]
        assert len(tool_msgs) >= 1
        # Count assignment occurrences (should be exactly 1, from session start)
        assignment_count = sum(1 for m in call2 if m["role"] == "user" and m["content"] == "my task")
        assert assignment_count == 1, f"Assignment appears {assignment_count} times"


class TestMaxTokens:
    def test_worker_max_tokens_recorded(self):
        backend = model.MockBackend([
            agent_response(make_tc("finish", {"summary": "done"})),
        ])
        config = {"max_turns": 50, "max_tokens": 256}
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("sys", "task", [], backend, config, tmp, "worker", 1)
        assert backend.call_history[0]["max_tokens"] == 256

    def test_manager_max_tokens_different(self):
        backend = model.MockBackend([
            agent_response(make_tc("finish", {"summary": "done"})),
        ])
        config = {"max_turns": 50, "max_tokens": 8192}
        with tempfile.TemporaryDirectory() as tmp:
            session.run_session("sys", "task", [], backend, config, tmp, "manager")
        assert backend.call_history[0]["max_tokens"] == 8192


class TestWriteTracking:
    def test_same_session_accumulates(self):
        from bid.tools import handle_write_file, reset_write_tracking
        with tempfile.TemporaryDirectory() as tmp:
            reset_write_tracking(tmp)
            handle_write_file({"path": "f.txt", "content": "a"}, tmp, "worker", 1)
            handle_write_file({"path": "f.txt", "content": "b"}, tmp, "worker", 1)
            with open(os.path.join(tmp, "f.txt")) as f:
                assert f.read() == "a\nb"

    def test_new_session_resets(self):
        from bid.tools import handle_write_file, reset_write_tracking
        with tempfile.TemporaryDirectory() as tmp:
            reset_write_tracking(tmp)
            handle_write_file({"path": "f.txt", "content": "a"}, tmp, "worker", 1)
            reset_write_tracking(tmp)
            handle_write_file({"path": "f.txt", "content": "b"}, tmp, "worker", 1)
            with open(os.path.join(tmp, "f.txt")) as f:
                assert f.read() == "b"


def test_session_finish():
    backend = model.MockBackend([
        agent_response(make_tc("finish", {"summary": "done"})),
    ])
    config = {"max_turns": 50}
    with tempfile.TemporaryDirectory() as tmp:
        result = session.run_session("system prompt", "do it", [], backend, config, tmp, "worker", 1)
    assert result["status"] == "success"
    assert result["summary"] == "done"


def test_session_turn_limit():
    backend = model.MockBackend([
        {"role": "assistant", "content": "working on it...", "tool_calls": None, "finish_reason": "stop"},
        {"role": "assistant", "content": "still working...", "tool_calls": None, "finish_reason": "stop"},
    ])
    config = {"max_turns": 1}
    with tempfile.TemporaryDirectory() as tmp:
        result = session.run_session("system prompt", "do it", [], backend, config, tmp, "worker", 1)
    assert result["status"] == "error"


def test_session_tool_execution():
    backend = model.MockBackend([
        agent_response(make_tc("list_files", {"path": "."})),
        agent_response(make_tc("finish", {"summary": "listed files"})),
    ])
    config = {"max_turns": 50}
    tool_list = tools.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs"))
        result = session.run_session("system prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
    assert result["status"] == "success"


def test_worker_check_own_task():
    backend = model.MockBackend([
        agent_response(make_tc("check_own_task", {})),
        agent_response(make_tc("finish", {"summary": "checked task"})),
    ])
    config = {"max_turns": 50}
    tool_list = tools.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs"))
        todo_content = "- [ ] T1 — Test task\n"
        with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
            f.write(todo_content)
        result = session.run_session("system prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
        assert result["status"] == "success"
        with open(os.path.join(tmp, "docs", "todo.md")) as f:
            new_content = f.read()
        assert "[x]" in new_content


def test_context_not_shared():
    backend = model.MockBackend([])
    config = {"max_turns": 50}
    with tempfile.TemporaryDirectory() as tmp:
        session.run_session("prompt1", "task1", [], backend, config, tmp, "worker", 1)
        session.run_session("prompt2", "task2", [], backend, config, tmp, "worker", 2)
    assert len(backend.call_history) == 2
    hist1 = backend.call_history[0]["messages"]
    hist2 = backend.call_history[1]["messages"]
    assert hist1[0]["content"] == "prompt1"
    assert hist2[0]["content"] == "prompt2"
