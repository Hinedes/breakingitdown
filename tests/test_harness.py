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


TODO_CONTENT = """# TODO

- [ ] T1 — Research three viable local inference backends and record the findings.
- [ ] T2 — Compare the researched backends.
- [ ] T3 — Produce a final recommendation.
"""

RESEARCH_ARTIFACT = "# Backend Research\n\n- llama.cpp: works\n- ollama: works\n- mlx: works\n"
COMPARISON_ARTIFACT = "# Comparison\n\nllama.cpp is best.\n"
RECOMMENDATION_ARTIFACT = "# Final Recommendation\n\nUse llama.cpp.\n"


def make_manager_init_script():
    return [
        agent_response([make_tool_call("write_file", {
            "path": "docs/todo.md",
            "content": TODO_CONTENT,
        })]),
        agent_response([make_tool_call("finish", {"summary": "Created TODO with 3 tasks"})]),
    ]


def make_worker_1_script():
    return [
        agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
        agent_response([make_tool_call("write_file", {
            "path": "docs/research/backends.md",
            "content": RESEARCH_ARTIFACT,
        })]),
        agent_response([make_tool_call("check_own_task", {})]),
    ]


def make_worker_2_script():
    return [
        agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/research/backends.md"})]),
        agent_response([make_tool_call("write_file", {
            "path": "docs/research/comparison.md",
            "content": COMPARISON_ARTIFACT,
        })]),
        agent_response([make_tool_call("check_own_task", {})]),
    ]


def make_worker_3_script():
    return [
        agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/research/backends.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/research/comparison.md"})]),
        agent_response([make_tool_call("write_file", {
            "path": "docs/final.md",
            "content": RECOMMENDATION_ARTIFACT,
        })]),
        agent_response([make_tool_call("check_own_task", {})]),
    ]


def make_manager_review_script_done():
    return [
        agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/research/backends.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/research/comparison.md"})]),
        agent_response([make_tool_call("read_file", {"path": "docs/final.md"})]),
        agent_response([make_tool_call("write_file", {
            "path": "docs/project-status.md",
            "content": "# Project Status\n\n## Status: DONE\n\nAll tasks completed successfully.",
        })]),
        agent_response([make_tool_call("finish", {"summary": "All work accepted"})]),
    ]


class TestFullFlow:
    def test_full_manager_worker_done_flow(self):
        responses = (make_manager_init_script()
                     + make_worker_1_script()
                     + make_worker_2_script()
                     + make_worker_3_script()
                     + make_manager_review_script_done())
        backend = model.MockBackend(responses)
        config = {
            "workspace": tempfile.mkdtemp(),
            "max_turns": 50,
        }
        try:
            result = harness.init_project("Research local inference backends and produce recommendation", config, backend=backend)
            assert result["status"] == "success"
            result = harness.run_project(config, backend=backend)
            assert result["status"] == "done"
            # Verify artifacts
            ws = config["workspace"]
            assert os.path.exists(os.path.join(ws, "docs/research/backends.md"))
            assert os.path.exists(os.path.join(ws, "docs/research/comparison.md"))
            assert os.path.exists(os.path.join(ws, "docs/final.md"))
            # Verify TODO is all checked
            with open(os.path.join(ws, "docs/todo.md")) as f:
                tasks = todo.parse_todo(f.read())
            assert todo.all_checked(tasks)
            # Verify project-status has DONE
            with open(os.path.join(ws, "docs/project-status.md")) as f:
                assert "DONE" in f.read()
            # Verify VC states
            from bid import vc as vc_mod
            vc = vc_mod.VersionControl(ws)
            assert vc.get_current() is not None
            log = vc.get_log()
            assert "Worker 1" in log
            assert "Worker 2" in log
            assert "Worker 3" in log
            assert "Manager (review)" in log
        finally:
            import shutil
            shutil.rmtree(config["workspace"], ignore_errors=True)


class TestWorkerPermissions:
    def test_worker_cannot_write_task_md(self):
        responses = [
            agent_response([make_tool_call("write_file", {
                "path": "docs/task.md",
                "content": "hacked",
            })]),
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Original\n")
            from bid import session as session_mod, tools as tools_mod
            tool_list = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
            result = session_mod.run_session("prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
            assert result["status"] == "success"
            with open(os.path.join(tmp, "docs", "task.md")) as f:
                assert f.read() == "# Original\n", "Worker should not have been able to modify task.md"

    def test_worker_cannot_write_project_status(self):
        responses = [
            agent_response([make_tool_call("write_file", {
                "path": "docs/project-status.md",
                "content": "## DONE",
            })]),
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            original = "# Project Status\n\nIn progress.\n"
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write(original)
            from bid import session as session_mod, tools as tools_mod
            tool_list = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
            result = session_mod.run_session("prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
            with open(os.path.join(tmp, "docs", "project-status.md")) as f:
                content = f.read()
            # Write should have been rejected — content must remain unchanged
            assert content == original, f"Expected unchanged, got: {content}"

    def test_worker_cannot_check_other_task(self):
        # Worker 2 tries to check T1
        responses = [
            agent_response([make_tool_call("check_own_task", {})]),  # This should check T2, not T1
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            todo_content = "- [ ] T1 — First\n- [ ] T2 — Second\n"
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_content)
            from bid import session as session_mod, tools as tools_mod
            tool_list = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=2)
            result = session_mod.run_session("prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 2)
            # T2 should be checked, T1 should not
            with open(os.path.join(tmp, "docs", "todo.md")) as f:
                tasks = todo.parse_todo(f.read())
            assert tasks[0]["checked"] is False, "T1 should remain unchecked"
            assert tasks[1]["checked"] is True, "T2 should be checked"


class TestManagerPermissions:
    def test_manager_cannot_write_worker_artifacts(self):
        responses = [
            agent_response([make_tool_call("write_file", {
                "path": "docs/research/foo.md",
                "content": "should be denied",
            })]),
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs", "research"))
            from bid import session as session_mod, tools as tools_mod
            tool_list = tools_mod.get_tools_for_role(permissions.ROLE_MANAGER)
            result = session_mod.run_session("prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_MANAGER)
            # The write should have been rejected with a permission error
            assert result["status"] == "success"
            assert not os.path.exists(os.path.join(tmp, "docs", "research", "foo.md"))


class TestVCRestore:
    def test_crashed_worker_restores_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n")
            from bid import vc as vc_mod
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            # Save s1 with a file
            with open(os.path.join(tmp, "docs", "important.md"), "w") as f:
                f.write("important data")
            vc.save_state("test", "saved")
            # Simulate crash: modify file
            with open(os.path.join(tmp, "docs", "important.md"), "w") as f:
                f.write("corrupted")
            # Create extra files
            with open(os.path.join(tmp, "crash.txt"), "w") as f:
                f.write("crash artifact")
            # Restore
            vc.restore("s1")
            # Verify
            with open(os.path.join(tmp, "docs", "important.md")) as f:
                assert f.read() == "important data"
            assert not os.path.exists(os.path.join(tmp, "crash.txt"))

    def test_rollback_deletes_later_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            from bid import vc as vc_mod
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            vc.save_state("a", "first")
            vc.save_state("b", "second")
            vc.save_state("c", "third")
            vc.restore("s1")
            states = vc._list_states()
            assert "s0" in states
            assert "s1" in states
            assert "s2" not in states
            assert "s3" not in states


class TestOnlyManagerSetsDone:
    def test_worker_finish_does_not_set_done(self):
        responses = [
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        config = {"max_turns": 50}
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# In progress\n")
            from bid import session as session_mod, tools as tools_mod
            tool_list = tools_mod.get_tools_for_role(permissions.ROLE_WORKER, worker_number=1)
            result = session_mod.run_session("prompt", "do it", tool_list, backend, config, tmp, permissions.ROLE_WORKER, 1)
            assert result["status"] == "success"
            with open(os.path.join(tmp, "docs", "project-status.md")) as f:
                assert "DONE" not in f.read()

    def test_finish_without_check_own_task_does_not_check(self):
        """Worker that finishes without checking its task leaves it unchecked."""
        from bid import vc as vc_mod
        responses = [
            agent_response([make_tool_call("write_file", {"path": "output.txt", "content": "work"})]),
            agent_response([make_tool_call("finish", {"summary": "done"})]),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — First task\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# In progress\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            vc = vc_mod.VersionControl(tmp)
            vc.init()
            from bid import harness
            config = {"workspace": tmp, "max_turns": 50}
            result = harness.run_project(config, backend=backend)
            # finish alone does not check the task
            with open(os.path.join(tmp, "docs", "todo.md")) as f:
                assert "[ ] T1" in f.read()
            assert result["status"] in ("error", "paused")


class TestManagerReopen:
    def test_manager_unchecks_and_reopens(self):
        """Manager can uncheck a task and worker redoes it."""
        responses = (
            make_manager_init_script()
            + make_worker_1_script()
            + make_worker_2_script()
            + make_worker_3_script()
            # Manager review: uncheck T1
            + [
                agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
                agent_response([make_tool_call("replace_text", {
                    "path": "docs/todo.md",
                    "old_text": "[x] T1",
                    "new_text": "[ ] T1",
                })]),
                agent_response([make_tool_call("write_file", {
                    "path": "docs/project-status.md",
                    "content": "# Project Status\n\nT1 needs rework.\n",
                })]),
                agent_response([make_tool_call("finish", {"summary": "Reopened T1 for rework"})]),
            ]
            # Worker 1 runs again
            + make_worker_1_script()
            # Manager review done (only final.md may not exist yet since W1 redo)
            + [
                agent_response([make_tool_call("read_file", {"path": "docs/todo.md"})]),
                agent_response([make_tool_call("read_file", {"path": "docs/research/backends.md"})]),
                agent_response([make_tool_call("write_file", {
                    "path": "docs/project-status.md",
                    "content": "# Project Status\n\n## Status: DONE\n\nT1 rework accepted.",
                })]),
                agent_response([make_tool_call("finish", {"summary": "T1 rework accepted"})]),
            ]
        )
        backend = model.MockBackend(responses)
        config = {
            "workspace": tempfile.mkdtemp(),
            "max_turns": 50,
        }
        try:
            result = harness.init_project("Research and recommend", config, backend=backend)
            assert result["status"] == "success"
            result = harness.run_project(config, backend=backend)
            assert result["status"] == "done"
        finally:
            import shutil
            shutil.rmtree(config["workspace"], ignore_errors=True)
