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


def test_session_finish():
    backend = model.MockBackend([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [make_tool_call("finish", {"summary": "done"})],
            "finish_reason": "tool_calls",
        }
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
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [make_tool_call("list_files", {"path": "."})],
            "finish_reason": "tool_calls",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [make_tool_call("finish", {"summary": "listed files"})],
            "finish_reason": "tool_calls",
        }
    ])
    config = {"max_turns": 50}
    tool_list = tools.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs"))
        result = session.run_session("system prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
    assert result["status"] == "success"


def test_worker_check_own_task():
    backend = model.MockBackend([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [make_tool_call("check_own_task", {})],
            "finish_reason": "tool_calls",
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [make_tool_call("finish", {"summary": "checked task"})],
            "finish_reason": "tool_calls",
        }
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
        result1 = session.run_session("prompt1", "task1", [], backend, config, tmp, "worker", 1)
        result2 = session.run_session("prompt2", "task2", [], backend, config, tmp, "worker", 2)
    assert len(backend.call_history) == 2
    # Each call should have different messages
    hist1 = backend.call_history[0]["messages"]
    hist2 = backend.call_history[1]["messages"]
    assert hist1[0]["content"] == "prompt1"
    assert hist2[0]["content"] == "prompt2"
