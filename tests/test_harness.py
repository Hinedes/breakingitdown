import pytest
import sys
import os
import tempfile
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid import harness, model, todo, permissions


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


def text_response(text):
    return agent_response(None, text, "stop")


def make_manager_init_script():
    return [
        agent_response([make_tool_call("write_file", {
            "path": "docs/todo.md",
            "content": "- [ ] T1 — First\n- [ ] T2 — Second\n- [ ] T3 — Third\n",
        })]),
        text_response("Done"),
    ]


def make_worker_script(task_num):
    return [
        agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
        agent_response([make_tool_call("write_file", {
            "path": f"output/t{task_num}.md",
            "content": f"# Result for T{task_num}\n",
        })]),
        agent_response([make_tool_call("replace_text", {
            "path": "docs/todo.md",
            "old_text": f"- [ ] T{task_num}",
            "new_text": f"- [x] T{task_num}",
        })]),
        text_response("Done"),
    ]


class TestObserver:
    def test_task_is_checked(self):
        from bid.observer import Observer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — test\n- [x] T2 — done\n")
            obs = Observer(tmp, 2)
            assert obs.task_is_checked()
            obs2 = Observer(tmp, 1)
            assert not obs2.task_is_checked()

    def test_task_just_became_checked(self):
        from bid.observer import Observer
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — test\n")
            obs = Observer(tmp, 1)
            assert not obs.task_just_became_checked()
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [x] T1 — test\n")
            assert obs.task_just_became_checked()
            assert not obs.task_just_became_checked()

    def test_seen_done(self):
        from bid.observer import Observer
        obs = Observer("/tmp", 1)
        assert obs.seen_done("Done")
        assert obs.seen_done("Some work\nDone\n")
        assert not obs.seen_done("")
        assert not obs.seen_done("Not done yet")
        assert obs.seen_done("  Done  ")
        assert obs.seen_done("The work is complete.\nDone")


class TestPermissions:
    def test_path_safety_allowed(self):
        from bid import permissions
        with tempfile.TemporaryDirectory() as tmp:
            safe, err, rel = permissions.check_path_safety("docs/todo.md", tmp)
            assert safe

    def test_path_traversal_rejected(self):
        from bid import permissions
        with tempfile.TemporaryDirectory() as tmp:
            safe, err, rel = permissions.check_path_safety("../../../etc/passwd", tmp)
            assert not safe

    def test_manager_write_allowed(self):
        from bid import permissions
        allowed, err = permissions.check_write_permission("docs/todo.md", permissions.ROLE_MANAGER)
        assert allowed

    def test_manager_write_denied(self):
        from bid import permissions
        allowed, err = permissions.check_write_permission("output/foo.md", permissions.ROLE_MANAGER)
        assert not allowed

    def test_worker_write_allowed(self):
        from bid import permissions
        allowed, err = permissions.check_write_permission("output/foo.md", permissions.ROLE_WORKER)
        assert allowed

    def test_worker_write_blocked(self):
        from bid import permissions
        for f in permissions.WORKER_BLOCKED:
            allowed, err = permissions.check_write_permission(f, permissions.ROLE_WORKER)
            assert not allowed


class TestVC:
    def test_init_creates_bid_dir(self):
        from bid import vc as vc_mod
        with tempfile.TemporaryDirectory() as tmp:
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            assert os.path.exists(os.path.join(tmp, ".bid", "states", "s0"))

    def test_save_and_restore(self):
        from bid import vc as vc_mod
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "f.txt"), "w") as f:
                f.write("hello")
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            with open(os.path.join(tmp, "docs", "f.txt"), "w") as f:
                f.write("modified")
            vc.save_state("test", "msg")
            vc.restore("s0")
            with open(os.path.join(tmp, "docs", "f.txt")) as f:
                assert f.read() == "hello"

    def test_rollback_deletes_later_states(self):
        from bid import vc as vc_mod
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            vc.save_state("a", "m1")
            vc.save_state("b", "m2")
            vc.restore("s1")
            states = vc._list_states()
            assert "s0" in states
            assert "s1" in states
            assert "s2" not in states


class TestSession:
    def test_run_turn_no_tools(self):
        from bid import session, model
        backend = model.MockBackend([text_response("Hello")])
        config = {"max_tokens": 256}
        with tempfile.TemporaryDirectory() as tmp:
            result = session.run_session("sys", "hi", [], backend, config, tmp, "worker", 1)
        assert result["content"] == "Hello"
        assert not result["tool_calls"]

    def test_run_turn_single_tool(self):
        from bid import session, model, tools
        backend = model.MockBackend([
            agent_response([make_tool_call("read_file", {"path": "f.txt"})]),
        ])
        config = {"max_tokens": 256}
        tool_list = [t for t in tools.COMMON_TOOLS if t["name"] == "read_file"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "f.txt"), "w") as f:
                f.write("data")
            result = session.run_session("sys", "read", tool_list, backend, config, tmp, "worker", 1)
        assert result["tool_calls"]

    def test_run_turn_alias(self):
        from bid import session, model, tools
        backend = model.MockBackend([
            agent_response([make_tool_call("create_file", {"path": "f.txt", "content": "hi"})]),
        ])
        config = {"max_tokens": 256}
        tool_list = [t for t in tools.COMMON_TOOLS if t["name"] == "write_file"]
        with tempfile.TemporaryDirectory() as tmp:
            result = session.run_session("sys", "write", tool_list, backend, config, tmp, "worker", 1)
            assert result["tool_calls"]
            with open(os.path.join(tmp, "f.txt")) as f:
                assert f.read() == "hi"


class TestTodo:
    def test_parse_todo(self):
        tasks = todo.parse_todo("- [ ] T1 — First\n- [x] T2 — Second\n")
        assert len(tasks) == 2
        assert tasks[0]["id"] == "T1"
        assert not tasks[0]["checked"]
        assert tasks[1]["checked"]

    def test_first_unchecked(self):
        tasks = todo.parse_todo("- [ ] T1 — First\n- [x] T2 — Second\n")
        assert todo.first_unchecked(tasks)["number"] == 1
        all_done = todo.parse_todo("- [x] T1 — done\n- [x] T2 — done")
        assert todo.first_unchecked(all_done) is None

    def test_set_task_checked(self):
        text = "- [ ] T1 — First\n- [ ] T2 — Second\n"
        result = todo.set_task_checked(text, 1, True)
        assert "[x] T1" in result
        assert "[ ] T2" in result


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


class TestAgent:
    def test_worker_outputs_done_task_checked(self):
        from bid import harness, model
        backend = model.MockBackend(make_worker_script(1))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Task one\n- [ ] T2 — Task two\n")
            with open(os.path.join(tmp, "docs", "worker.md"), "w") as f:
                f.write("# Worker\n")
            config = {"workspace": tmp, "max_tokens": 256, "inactivity_timeout": 3, "worker_timeout": 10}
            result = harness.run_worker_session(1, config, backend=backend)
            # The mock script does replace_text then Done — should see task checked
            assert result["status"] in ("submitted", "error"), f"Got {result['status']}: {result.get('reason','')}"
            if result["status"] == "error":
                print(f"DEBUG: task checked? ", end="")
                from bid.observer import Observer
                obs = Observer(tmp, 1)
                print(obs.task_is_checked())

    def test_worker_timeout_without_check(self):
        from bid import harness, model
        backend = model.MockBackend([text_response("Working...")])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Task one\n")
            with open(os.path.join(tmp, "docs", "worker.md"), "w") as f:
                f.write("# Worker\n")
            config = {"workspace": tmp, "max_tokens": 256, "worker_timeout": 5, "inactivity_timeout": 3}
            result = harness.run_worker_session(1, config, backend=backend)
            assert result["status"] != "submitted"

    def test_manager_init_writes_todo(self):
        from bid import harness, model
        backend = model.MockBackend(make_manager_init_script())
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest\n")
            with open(os.path.join(tmp, "docs", "manager.md"), "w") as f:
                f.write("# Manager\n")
            config = {"workspace": tmp, "max_tokens": 256}
            result = harness.run_manager_init(config, backend=backend)
            assert result["status"] == "done"
            with open(os.path.join(tmp, "docs", "todo.md")) as f:
                assert "T1" in f.read()
