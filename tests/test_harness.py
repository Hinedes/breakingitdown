import json
import os
import sys
import tempfile
import types

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


PRESERVED_MANAGER_RESPONSES = (
    """- [ ] Implement fail-closed behavior in `solve_point` when no physically valid beam-constrained solution exists.
- [ ] Update affected callers to handle the new failure state gracefully.
- [ ] Add regression tests validating the fix prevents invalid reconstructions.
- [ ] Run focused ARGUS tests related to point reconstruction.
- [ ] Execute complete ARGUS test suite and verify stability.""",
    """- [ ] Implement fail-closed behavior in `solve_point` when no physically valid beam-constrained solution exists.
- [ ] Update affected callers to handle the new failure state gracefully.
- [ ] Add regression tests validating the fix prevents invalid reconstructions.
- [ ] Run focused ARGUS tests related to point reconstruction.
- [ ] Execute complete ARGUS test suite and verify stability.""",
    """- [ ] Implement fail-closed behavior in `solve_point` when no physically valid beam-constrained solution exists.
- [ ] Update affected callers to handle the new failure state gracefully.
- [ ] Add regression tests validating the fix prevents invalid reconstructions.
- [ ] Run focused ARGUS tests related to point reconstruction.
- [ ] Execute complete ARGUS test suite and verify stability.""",
)


def prepare_workspace(tmp, todo_text):
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
        file.write(todo_text)
    with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as file:
        file.write("# Task\n\nDo the thing.\n")
    with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as file:
        file.write("# Project Status\n\nInitialized.\n")
    with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as file:
        file.write("# Decisions\n\n")
    harness.ensure_workspace(tmp)
    vc.VersionControl(tmp).init()


def make_existing_project(workspace):
    files = {
        ".git/HEAD": b"ref: refs/heads/main\n",
        ".gitignore": b"__pycache__/\n",
        "README.md": b"# Existing project\n",
        "argus/solve.py": b"def solve():\n    return 42\n",
        "tests/test_existing.py": b"def test_existing():\n    assert True\n",
        "script": b"#!/bin/sh\necho existing\n",
    }
    for rel, content in files.items():
        path = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(content)
    os.chmod(os.path.join(workspace, "script"), 0o755)
    os.symlink("argus/solve.py", os.path.join(workspace, "solve-link"))
    return files


def assert_existing_project(workspace, files):
    for rel, content in files.items():
        with open(os.path.join(workspace, rel), "rb") as file:
            assert file.read() == content
    assert os.stat(os.path.join(workspace, "script")).st_mode & 0o111 == 0o111
    link = os.path.join(workspace, "solve-link")
    assert os.path.islink(link)
    assert os.readlink(link) == "argus/solve.py"


class TestInitProject:
    def test_existing_project_is_preserved_and_snapshotted(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "project")
            os.mkdir(workspace)
            files = make_existing_project(workspace)
            backup = os.path.join(tmp, ".bid_backup")
            manager_finished = False
            original_rmtree = harness.shutil.rmtree

            class Backend(model.MockBackend):
                def run(self, *args, **kwargs):
                    nonlocal manager_finished
                    manager_finished = True
                    return super().run(*args, **kwargs)

            def tracked_rmtree(path, *args, **kwargs):
                if path == backup:
                    assert manager_finished
                return original_rmtree(path, *args, **kwargs)

            monkeypatch.setattr(harness.shutil, "rmtree", tracked_rmtree)
            result = harness.init_project("Inspect existing project", config(workspace), backend=Backend([text_response(todo_item(1, "Inspect"))]))

            assert result["status"] == "success"
            assert_existing_project(workspace, files)
            assert os.path.isdir(os.path.join(workspace, ".bid"))
            assert vc.VersionControl(workspace).get_current() == "s1"
            state = os.path.join(workspace, ".bid", "states", "s0")
            for rel, content in files.items():
                with open(os.path.join(state, rel), "rb") as file:
                    assert file.read() == content
            assert not os.path.exists(backup)

    def test_worker_can_read_existing_project_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "project")
            os.mkdir(workspace)
            make_existing_project(workspace)
            cfg = config(workspace)
            assert harness.init_project("Read existing file", cfg, backend=model.MockBackend([text_response(todo_item(1, "Read"))]))["status"] == "success"

            backend = model.MockBackend([text_response("READ argus/solve.py"), text_response("Done")])
            result = harness.run_worker_session(1, cfg, backend=backend)

            assert result["status"] == "submitted"
            assert "def solve():\n    return 42" in backend.call_history[1]["messages"][-1]["content"]

    def test_manager_failure_restores_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "project")
            os.mkdir(workspace)
            files = make_existing_project(workspace)
            backend = model.MockBackend([text_response("invalid")] * 3)

            result = harness.init_project("Fail manager", config(workspace), backend=backend)

            assert result["status"] == "error"
            assert len(backend.call_history) == 3
            assert_existing_project(workspace, files)
            assert not os.path.exists(os.path.join(workspace, ".bid"))

    def test_copy_failure_restores_existing_project(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "project")
            os.mkdir(workspace)
            files = make_existing_project(workspace)

            def fail_copytree(source, destination, *args, **kwargs):
                os.makedirs(destination)
                with open(os.path.join(destination, "partial"), "w", encoding="utf-8") as file:
                    file.write("partial")
                raise OSError("copy failed")

            monkeypatch.setattr(harness.shutil, "copytree", fail_copytree)
            result = harness.init_project("Fail copy", config(workspace), backend=model.MockBackend())

            assert result == {"status": "error", "reason": "copy failed"}
            assert_existing_project(workspace, files)
            assert not os.path.exists(os.path.join(workspace, ".bid"))

    def test_absent_workspace_still_initializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = os.path.join(tmp, "new-project")

            result = harness.init_project("Create project", config(workspace), backend=model.MockBackend([text_response(todo_item(1, "Create"))]))

            assert result["status"] == "success"
            assert os.path.isdir(os.path.join(workspace, ".bid"))


def test_fenced_qwen_read_run_response_parses_without_fence_or_terminators():
    response = """```bash
READ README.md
END READ
RUN ls -la
END RUN
```"""

    assert adapter._parse_content_into_turns(response) == [
        {"type": "READ", "path": "README.md"},
        {"type": "RUN", "command": "ls -la"},
    ]


def test_write_body_preserves_literal_think_text():
    response = "WRITE note.md\n<think>domain content</think>\nEND WRITE"

    assert adapter._parse_content_into_turns(response) == [
        {"type": "WRITE", "path": "note.md", "content": "<think>domain content</think>"},
    ]


# ── BID-v2 parser interface: EOF WRITE + unknown-command feedback ──

def test_eof_write_accepted_when_stop():
    """EOF-terminated WRITE with finish_reason=stop is accepted."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nprint(1)", "stop")
    writes = [c for c in cmds if c["type"] == "WRITE"]
    assert len(writes) == 1
    assert writes[0]["path"] == "a.py"
    assert writes[0]["content"] == "print(1)"


def test_eof_write_rejected_when_length():
    """EOF-terminated WRITE with finish_reason=length is rejected."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nbody", "length")
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    assert len(unterminated) == 1


def test_eof_write_rejected_when_trailing_cmd():
    """EOF WRITE with a trailing RUN after the body is rejected."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nbody\nRUN python -c pass", "stop")
    writes = [c for c in cmds if c["type"] == "WRITE"]
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    assert len(writes) == 0


def test_eof_write_rejected_empty_body():
    """Empty EOF body is rejected."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\n", "stop")
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    assert len(unterminated) == 1


def test_explicit_end_write_still_accepted():
    """Explicit END WRITE is still accepted (unchanged from original)."""
    cmds = adapter._parse_content_into_turns("WRITE a.md\nhello\nEND WRITE\nREAD b.md", "stop")
    writes = [c for c in cmds if c["type"] == "WRITE" and c["path"] == "a.md"]
    reads = [c for c in cmds if c["type"] == "READ"]
    assert len(writes) == 1 and writes[0]["content"] == "hello"
    assert len(reads) == 1


def test_find_unknown_commands_catches_listdirs():
    """LISTDIRS is detected as an unknown command."""
    assert adapter._find_unknown_commands("READ a\nLISTDIRS", [{"type":"READ"}]) == ["LISTDIRS"]


def test_find_unknown_commands_ignores_prose():
    """Prose (3+ tokens, not all-caps) is not flagged."""
    assert adapter._find_unknown_commands("This is prose", []) == []


def test_find_unknown_commands_inside_write_body():
    """Content inside a WRITE body is not flagged."""
    assert adapter._find_unknown_commands("WRITE a.py\nARGUS knows\ngo DO something\nEND WRITE", [{"type":"WRITE"}]) == []


def test_find_unknown_readme_not_uppercase():
    """README.md (mixed case at start) is not flagged as an uppercase verb."""
    assert adapter._find_unknown_commands("README.md has docs", []) == []


def test_find_unknown_reading_not_read():
    """READING is not matched as a READ command."""
    assert adapter._find_unknown_commands("READING the source", []) == []


def test_standalone_done_valid():
    """Standalone 'Done' is valid (not flagged as unknown)."""
    cmds = adapter._parse_content_into_turns("Done")
    assert any(c["type"] == "Done" for c in cmds)
    assert adapter._find_unknown_commands("Done", cmds) == []


def test_done_followed_by_text_flagged_unknown():
    """'Done with task' is not a valid Done command; it IS flagged as unknown."""
    cmds = adapter._parse_content_into_turns("Done with task")
    assert not any(c["type"] == "Done" for c in cmds)
    # _find_unknown_commands takes the raw content and the parsed commands
    assert adapter._find_unknown_commands("Done with task\n", cmds) == ["Done with task"]


def test_prose_containing_done_not_flagged():
    """Ordinary prose containing the word 'done' is not flagged."""
    assert adapter._find_unknown_commands("the task is done now", []) == []


def test_done_mixed_with_valid_read():
    """A valid READ followed by 'Done now' executes READ and flags Done."""
    cmds = adapter._parse_content_into_turns("READ a.txt\nDone now", "stop")
    reads = [c for c in cmds if c["type"] == "READ"]
    assert len(reads) == 1
    unk = adapter._find_unknown_commands("READ a.txt\nDone now", cmds)
    assert "Done now" in unk


# ── Implicit EOF WRITE default-off ─────────────────────────────────

def test_implicit_write_disabled_by_default():
    """Default finish_reason=None rejects EOF-terminated writes."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nbody")
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    writes = [c for c in cmds if c["type"] == "WRITE"]
    assert len(unterminated) == 1, f"expected WRITE_UNTERMINATED, got {cmds}"
    assert len(writes) == 0


def test_implicit_write_explicit_end_works_without_flag():
    """Explicit END WRITE works when implicit writes are disabled."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nbody\nEND WRITE")
    writes = [c for c in cmds if c["type"] == "WRITE"]
    assert len(writes) == 1
    assert writes[0]["content"] == "body"


def test_implicit_write_body_preserved_in_unterminated():
    """When rejected, the body is preserved in WRITE_UNTERMINATED (not lost)."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nreal content here")
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    assert len(unterminated) == 1
    assert unterminated[0]["path"] == "a.py"


# ── Run-timeout configuration ──────────────────────────────────────

def test_run_timeout_default_sixty():
    """Default run_timeout is 60 seconds."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cfg = harness.get_config()
        # get_config returns a dict; run_timeout key must exist
        assert "run_timeout" in cfg, f"missing run_timeout in config: {cfg}"
        # Without BID_RUN_TIMEOUT env, default should be 60
        # cfg was built from current environment; check default via direct call
        from bid import harness as h
        # patch environ temporarily
        old = os.environ.get("BID_RUN_TIMEOUT")
        if "BID_RUN_TIMEOUT" in os.environ:
            del os.environ["BID_RUN_TIMEOUT"]
        cfg2 = h.get_config()
        assert cfg2["run_timeout"] == 60, f"expected 60, got {cfg2['run_timeout']}"
        if old is not None:
            os.environ["BID_RUN_TIMEOUT"] = old


def test_run_timeout_env_override():
    """BID_RUN_TIMEOUT=900 produces run_timeout=900."""
    import tempfile
    from bid import harness as h
    old = os.environ.get("BID_RUN_TIMEOUT")
    os.environ["BID_RUN_TIMEOUT"] = "900"
    cfg = h.get_config()
    assert cfg["run_timeout"] == 900, f"expected 900, got {cfg['run_timeout']}"
    if old is not None:
        os.environ["BID_RUN_TIMEOUT"] = old
    else:
        del os.environ["BID_RUN_TIMEOUT"]


def test_run_timeout_invalid_env_fails():
    """Invalid BID_RUN_TIMEOUT value raises ValueError."""
    import tempfile
    old = os.environ.get("BID_RUN_TIMEOUT")
    os.environ["BID_RUN_TIMEOUT"] = "not-a-number"
    try:
        from bid import harness as h
        cfg = h.get_config()
        # Should either raise ValueError or produce a nonsense value
        assert False, "expected ValueError for invalid BID_RUN_TIMEOUT"
    except ValueError:
        pass  # expected
    finally:
        if old is not None:
            os.environ["BID_RUN_TIMEOUT"] = old
        else:
            del os.environ["BID_RUN_TIMEOUT"]


# ── Implicit write evidence counting (integration-level) ───────────

def test_implicit_write_tagged_in_parser():
    """EOF-accepted WRITE carries implicit=True flag."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nprint(1)", "stop")
    writes = [c for c in cmds if c["type"] == "WRITE"]
    assert len(writes) == 1
    assert writes[0].get("implicit") is True, f"missing implicit flag: {writes[0]}"


def test_explicit_write_not_tagged_implicit():
    """END-WRITE-terminated WRITE does NOT carry implicit flag."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nprint(1)\nEND WRITE", "stop")
    writes = [c for c in cmds if c["type"] == "WRITE"]
    assert len(writes) == 1
    assert writes[0].get("implicit") is None, f"unexpected implicit flag: {writes[0]}"


def test_rejected_implicit_write_not_tagged():
    """Rejected implicit write (length) does NOT carry implicit flag."""
    cmds = adapter._parse_content_into_turns("WRITE a.py\nbody", "length")
    unterminated = [c for c in cmds if c["type"] == "WRITE_UNTERMINATED"]
    assert len(unterminated) == 1


def test_run_timeout_propagated_to_subprocess(monkeypatch):
    """WorkerAdapter configured with run_timeout=900 passes timeout=900 to subprocess."""
    import tempfile, subprocess as sp
    captured = {}
    original_run = sp.run

    def fake_run(argv, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["argv"] = argv
        # Return a fake CompletedProcess
        return sp.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(sp, "run", fake_run)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config(tmp, run_timeout=900)
        wa = adapter.WorkerAdapter(cfg, 1)
        result = wa._run_command("python -c 'print(1)'")
        assert captured.get("timeout") == 900, f"expected timeout=900, got {captured}"


def test_implicit_write_count_wired():
    """WorkerAdapter has _implicit_write_count and the parser tags implicit writes."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = config(tmp)
        wa = adapter.WorkerAdapter(cfg, 1)
        assert hasattr(wa, "_implicit_write_count")
        assert wa._implicit_write_count == 0
    # Parser tags: verified by test_implicit_write_tagged_in_parser
    # Parser does NOT tag: verified by test_explicit_write_not_tagged_implicit


class TestReviewerContracts:
    def test_manager_init_uses_manager_checklist_prompt(self):
        backend = model.MockBackend([text_response("- [ ] Inspect the project")])

        with tempfile.TemporaryDirectory() as tmp:
            result = harness.init_project("Inspect the project", config(tmp), backend=backend)

            assert result["status"] == "success"
            with open(os.path.join(tmp, "docs", "manager.md"), encoding="utf-8") as file:
                system = file.read()
            assert backend.call_history[0]["messages"][0]["content"] == system
            assert "- [ ] Description" in system
            assert "numbered checklist" not in system
            assert "T1" not in system
            assert "T2" not in system
            assert "- [ ] Description" in backend.call_history[0]["messages"][1]["content"]
            assert "T1" not in backend.call_history[0]["messages"][1]["content"]

    def test_manager_preserved_unlabelled_responses_get_harness_ids(self):
        for response in PRESERVED_MANAGER_RESPONSES:
            with tempfile.TemporaryDirectory() as tmp:
                result = harness.init_project("Fix solve_point", config(tmp), backend=model.MockBackend([text_response(response)]))
                assert result["status"] == "success"
                with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                    todo_text = file.read()
                tasks = todo.parse_todo(todo_text)
                assert [task["id"] for task in tasks] == ["T1", "T2", "T3", "T4", "T5"]
                assert tasks[0]["description"].startswith("Implement fail-closed")

    def test_manager_normalizes_wrong_or_duplicate_ids_by_order(self):
        todo = adapter.ManagerInitAdapter._todo("- [ ] T7 — First\n* [ ] T7 — Second\n- [ ] T99 — Third")
        assert todo == "- [ ] T1 — First\n- [ ] T2 — Second\n- [ ] T3 — Third"

    def test_manager_rejects_commentary_and_invalid_items(self):
        assert adapter.ManagerInitAdapter._todo("- [ ] First\nNote: do this too") is None
        for invalid in ("- [x] Done", "- [ ]", "- [] Missing space", "1. First", "Just prose"):
            assert adapter.ManagerInitAdapter._todo(invalid) is None

    def test_task_review_retries_with_reviewer_contract(self):
        correction = "No valid reviewer verdict was found. Return only:\nACCEPT followed by Reason:, or REWORK followed by Reason:."
        backend = model.MockBackend([
            text_response("- [ ] T1 — Review the candidate"),
            text_response("ACCEPT\nReason: The candidate satisfies the task."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Review the candidate"))
            with open(os.path.join(tmp, "docs", "manager.md"), "w", encoding="utf-8") as file:
                file.write("Create only a numbered checklist.")

            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s0").run(backend)

            assert result["verdict"] == "ACCEPT"
            assert "numbered checklist" not in backend.call_history[0]["messages"][0]["content"]
            assert backend.call_history[1]["messages"][-1]["content"] == correction

    def test_completion_review_retries_with_completion_contract(self):
        correction = "No valid completion verdict was found. Return only COMPLETE with Reason:, or MISSING followed by one or more missing-deliverable bullets."
        backend = model.MockBackend([
            text_response("Analysis: the candidate is ready."),
            text_response("COMPLETE\nReason: The request is satisfied."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Review the candidate", checked=True))
            with open(os.path.join(tmp, "docs", "manager.md"), "w", encoding="utf-8") as file:
                file.write("Create only a numbered checklist.")

            result = adapter.CompletionReviewAdapter(config(tmp)).run(backend)

            assert result["verdict"] == "COMPLETE"
            assert "numbered checklist" not in backend.call_history[0]["messages"][0]["content"]
            assert backend.call_history[1]["messages"][-1]["content"] == correction


class TestWorkerSession:
    def test_worker_can_finish_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "No-op task"))
            backend = model.MockBackend([text_response("Done")])
            result = harness.run_worker_session(1, config(tmp), backend=backend)
            assert result["status"] == "submitted"
            assert result["termination"] == "normal"
            assert not backend.call_history[0]["messages"][0]["content"].startswith("/no_think")
            assert vc.VersionControl(tmp).get_current() == "s1"

    def test_analysis_only_response_gets_command_only_retry(self):
        correction = "No executable BID command was found. Respond only with actual READ, WRITE, RUN, or Done commands. Do not explain or describe the commands."
        backend = model.MockBackend([
            text_response("I would inspect README.md first."),
            text_response("READ README.md"),
            text_response("Done"),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Inspect README"))
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as file:
                file.write("project notes\n")

            result = harness.run_worker_session(1, config(tmp), backend=backend)

            assert result["status"] == "submitted"
            assert backend.call_history[1]["messages"][-1]["content"] == correction
            assert backend.call_history[2]["messages"][-1]["content"] == "project notes\n"

    def test_qwen_thinking_payload_preserves_reasoning(self, monkeypatch):
        captured = {}

        class Response:
            status_code = 200
            text = ""

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"role": "assistant", "reasoning": "private reasoning", "content": "Done"}}]}

        class Client:
            def __init__(self, timeout):
                captured["timeout"] = timeout

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def post(self, endpoint, json):
                captured["endpoint"] = endpoint
                captured["payload"] = json
                return Response()

        monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(Client=Client))
        response = model.LlamaCppBackend("http://model", "qwen", max_tokens=32768).run([], [])

        assert response["content"] == "Done"
        assert response["reasoning"] == "private reasoning"
        assert captured["payload"]["max_tokens"] == 32768
        assert "options" not in captured["payload"]
        assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": True}
        assert {key: captured["payload"][key] for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "repetition_penalty")} == {
            "temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0, "repetition_penalty": 1.0,
        }

    def test_run_command_maps_python_to_interpreter(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            runner = adapter.WorkerAdapter(config(tmp), 1)
            seen = {}

            def fake_run(argv, **kwargs):
                seen["argv"] = argv

                class Result:
                    stdout = ""
                    stderr = ""
                    returncode = 0

                return Result()

            monkeypatch.setattr(adapter.subprocess, "run", fake_run)
            result = runner._run_command("python -c 'print(1)'")
            assert seen["argv"][:2] == [sys.executable, "-c"]
            assert "command: python -c 'print(1)'" in result

    def test_run_command_decodes_timeout_bytes(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            runner = adapter.WorkerAdapter(config(tmp), 1)

            def fake_run(argv, **kwargs):
                raise adapter.subprocess.TimeoutExpired(argv, 1, output=b"stdout-\xff", stderr=b"stderr-\xfe")

            monkeypatch.setattr(adapter.subprocess, "run", fake_run)
            result = runner._run_command("python -c 'print(1)'")
            assert "timed_out: yes" in result
            assert "exit_code: -1" in result
            assert "stdout:\nstdout-" in result
            assert "stderr:\nstderr-" in result
            assert "b'" not in result

    def test_direct_deletions_cannot_remove_workspace_or_control_state(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Deletion guard"))
            runner = adapter.WorkerAdapter(config(tmp), 1)

            def fail_if_run(*args, **kwargs):
                raise AssertionError("blocked deletion reached subprocess")

            monkeypatch.setattr(adapter.subprocess, "run", fail_if_run)
            for command in (
                "rm -rf .",
                "rm -rf ..",
                "rm -rf docs",
                "rm -rf .bid",
                "rm -rf /absolute/path",
                "rm docs/todo.md",
                "rmdir docs",
                "unlink docs/todo.md",
            ):
                result = runner._run_command(command)
                assert "error: deletion denied:" in result
                assert "exit_code: 126" in result
                assert runner._run_evidence[-1]["denied_deletion"]

    def test_direct_deletions_allow_ordinary_project_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Deletion guard"))
            runner = adapter.WorkerAdapter(config(tmp), 1)

            os.makedirs(os.path.join(tmp, "build", "nested"))
            assert "result: success" in runner._run_command("rm -rf build")
            assert not os.path.exists(os.path.join(tmp, "build"))

            os.mkdir(os.path.join(tmp, "empty"))
            assert "result: success" in runner._run_command("rmdir empty")
            assert not os.path.exists(os.path.join(tmp, "empty"))

            output = os.path.join(tmp, "output.txt")
            with open(output, "w", encoding="utf-8") as file:
                file.write("temporary\n")
            assert "result: success" in runner._run_command("unlink output.txt")
            assert not os.path.exists(output)

    def test_worker_reads_project_file_after_initial_prompt(self):
        class PromptBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0

            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })

                prompt = messages[-1]["content"] if messages else ""
                system = messages[0]["content"]

                if self.step == 0:
                    assert "Read docs/worker.md" not in system
                    assert "{worker_number}" not in system
                    assert "Only use these commands" not in messages[1]["content"]
                    assert "Task T1:" in prompt
                    self.step = 1
                    return text_response("READ README.md")

                if self.step == 1:
                    assert "project note" in prompt
                    self.step = 2
                    return text_response("Done")

                if prompt.startswith("# Review Assignment"):
                    return text_response("ACCEPT\nReason: Fine.")

                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")

                raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Read a project file"))
            with open(os.path.join(tmp, "README.md"), "w", encoding="utf-8") as file:
                file.write("project note\n")

            result = harness.run_project(config(tmp), backend=PromptBackend())
            assert result["status"] == "done"

            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                assert "[x] T1" in file.read()

    def test_workspace_listing_skips_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ok.txt"), "w", encoding="utf-8") as file:
                file.write("hello")
            os.makedirs(os.path.join(tmp, "__pycache__"), exist_ok=True)
            with open(os.path.join(tmp, "__pycache__", "bad.pyc"), "wb") as file:
                file.write(b"\x80\x04\x95\x01\x00\x00\x00\x00\x00\x00\x00\x9c")

            listing = adapter._workspace_listing(tmp)
            assert "ok.txt" in listing
            assert "bad.pyc" not in listing


class TestRunProject:
    def test_accept_continues_to_next_task_then_runs_completion_review(self):
        backend = model.MockBackend([
            text_response("Done"),
            text_response("ACCEPT\nReason: First task complete."),
            text_response("Done"),
            text_response("ACCEPT\nReason: Second task complete."),
            text_response("COMPLETE\nReason: All tasks complete."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project(
                "Complete two tasks",
                cfg,
                backend=model.MockBackend([text_response(todo_item(1, "First") + todo_item(2, "Second"))]),
            )["status"] == "success"

            result = harness.run_project(cfg, backend=backend)

            assert result["status"] == "done"
            worker_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].lstrip().startswith("Task T")
            ]
            assert len(worker_prompts) == 2
            assert "Task T1:" in worker_prompts[0]
            assert "Task T2:" in worker_prompts[1]
            completion_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Completion Review")
            ]
            assert len(completion_prompts) == 1
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                assert "[x] T1" in file.read()
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                assert "[x] T2" in file.read()

    def test_rework_then_accept_retries_same_task(self):
        responses = [
            text_response(todo_item(1, "Write result")),
            text_response("WRITE notes.txt\ndraft\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Draft too weak."),
            text_response("WRITE notes.txt\nfinal\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ]
        backend = model.MockBackend(responses)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Write result", cfg, backend=backend)["status"] == "success"
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as file:
                file.write("BASE_SENTINEL")
            vc.VersionControl(tmp).save_state("prep", "seed sentinel")
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as file:
                assert file.read() == "final"
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                assert "[x] T1" in file.read()
            assert vc.VersionControl(tmp).get_current() == "s4"
            review_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            ]
            assert len(review_prompts) == 2
            assert "BASE_SENTINEL" in review_prompts[1]
            assert "DRAFT_SENTINEL" not in review_prompts[1]


class TestResumeBehavior:
    def test_interrupt_resume_preserves_task_base(self):
        init_backend = model.MockBackend([text_response(todo_item(1, "Update notes"))])
        first_worker_backend = model.MockBackend([text_response("WRITE notes.txt\ndraft\nEND WRITE\nDone")])
        first_review_backend = model.MockBackend([text_response("REWORK\nReason: Draft too weak.")])
        resume_backend = model.MockBackend([
            text_response("WRITE notes.txt\nfinal\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Update notes", cfg, backend=init_backend)["status"] == "success"
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as file:
                file.write("BASE_SENTINEL")
            base_state = vc.VersionControl(tmp).save_state("prep", "seed sentinel")

            worker_result = harness.run_worker_session(1, cfg, backend=first_worker_backend)
            assert worker_result["status"] == "submitted"
            assert worker_result["base_state"] == base_state

            review_result = adapter.TaskReviewAdapter(cfg, 1, base_state=worker_result["base_state"]).run(first_review_backend)
            assert review_result["verdict"] == "REWORK"

            result = harness.run_project(cfg, backend=resume_backend)
            assert result["status"] == "done"

            review_prompts = [
                request["messages"][1]["content"]
                for request in resume_backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            ]
            assert len(review_prompts) == 1
            assert "BASE_SENTINEL" in review_prompts[0]
            assert "draft" not in review_prompts[0]

    def test_stalled_worker_respawns_and_preserves_workspace_changes(self):
        class RespawnBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.session = 0
                self.prev_len = None
                self.step = 0
                self.saw_preserved_draft = False

            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })

                prompt = messages[1]["content"] if len(messages) > 1 else ""
                if prompt.startswith("# Review Assignment"):
                    return text_response("ACCEPT\nReason: Fixed.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")

                if self.prev_len is None:
                    self.session = 1
                    self.step = 0
                elif self.prev_len != 2 and len(messages) == 2:
                    self.session += 1
                    self.step = 0
                self.prev_len = len(messages)

                if self.session == 1:
                    if self.step == 0:
                        self.step += 1
                        return text_response("WRITE notes.txt\ndraft\nEND WRITE")
                    self.step += 1
                    return text_response("READ notes.txt")

                if self.session == 2:
                    if self.step == 0:
                        self.step += 1
                        return text_response("READ notes.txt")
                    if self.step == 1:
                        self.saw_preserved_draft = any("draft" in message.get("content", "") for message in messages)
                        self.step += 1
                        return text_response("WRITE notes.txt\nfinal\nEND WRITE\nDone")
                    raise AssertionError("unexpected extra worker turn after respawn")

                raise AssertionError(f"unexpected worker session {self.session}")

        init_backend = model.MockBackend([text_response(todo_item(1, "Update notes"))])
        backend = RespawnBackend()

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp, repeat_action_limit=1)
            assert harness.init_project("Update notes", cfg, backend=init_backend)["status"] == "success"
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as file:
                file.write("BASE_SENTINEL")
            base_state = vc.VersionControl(tmp).save_state("prep", "seed sentinel")

            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            assert backend.saw_preserved_draft

            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as file:
                assert file.read() == "final"

            worker_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) == 2 and request["messages"][1]["content"].lstrip().startswith("Task T1:")
            ]
            assert len(worker_prompts) == 2

            review_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            ]
            assert len(review_prompts) == 1
            assert "BASE_SENTINEL" in review_prompts[0]

            completion_prompts = [
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Completion Review")
            ]
            assert len(completion_prompts) == 1
            assert vc.VersionControl(tmp).get_current() == "s3"

    def test_resume_all_checked_runs_final_review(self):
        backend = model.MockBackend([text_response("COMPLETE\nReason: Done.")])

        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "First", checked=True) + todo_item(2, "Second", checked=True))
            vc.VersionControl(tmp).save_state("prep", "all checked but incomplete")

            result = harness.run_project(config(tmp), backend=backend)
            assert result["status"] == "done"
            assert backend.call_history[0]["messages"][1]["content"].startswith("# Completion Review")

    def test_completion_review_appends_plain_tasks(self):
        class CrashAfterResponses(model.MockBackend):
            def __init__(self, responses, crash_on):
                super().__init__(responses)
                self.crash_on = crash_on

            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })
                if self.call_index == self.crash_on:
                    raise RuntimeError("simulated interruption")
                if self.call_index < len(self.responses):
                    response = self.responses[self.call_index]
                    self.call_index += 1
                    return response
                raise RuntimeError("simulated interruption")

        first_backend = CrashAfterResponses([
            text_response("WRITE docs/work/T1.md\none\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fine."),
            text_response("MISSING\n- Follow-up deliverable"),
        ], crash_on=3)
        resume_backend = model.MockBackend([
            text_response("WRITE docs/work/T2.md\ntwo\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fine."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Build one file", cfg, backend=model.MockBackend([text_response(todo_item(1, "First"))]))["status"] == "success"
            first = harness.run_project(cfg, backend=first_backend)
            assert first["status"] == "error"
            assert vc.VersionControl(tmp).get_current() == "s2"
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                todo_text = file.read()
            assert "[x] T1" in todo_text
            assert "T2" in todo_text

            second = harness.run_project(cfg, backend=resume_backend)
            assert second["status"] == "done"
            review_prompts = [
                request["messages"][1]["content"]
                for request in first_backend.call_history + resume_backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            ]
            assert len(review_prompts) == 2
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                todo_text = file.read()
            assert "[x] T1" in todo_text
            assert "[x] T2" in todo_text
            assert vc.VersionControl(tmp).get_current() == "s3"

    def test_worker_write_run_and_submit_candidate(self):
        backend = model.MockBackend([
            text_response("WRITE hello.txt\nhello\nEND WRITE"),
            text_response("RUN python -B -m pytest -q"),
            text_response("Done"),
            text_response("ACCEPT\nReason: File and test match."),
            text_response("COMPLETE\nReason: Done."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)

            with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as file:
                file.write("# Task\n\nEdit hello.txt and verify it with pytest.\n")
            with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
                file.write(todo_item(1, "Edit hello.txt and verify it with pytest"))
            with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as file:
                file.write("# Project Status\n\nInitialized.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as file:
                file.write("# Decisions\n\n")
            with open(os.path.join(tmp, "tests", "test_hello.py"), "w", encoding="utf-8") as file:
                file.write(
                    "from pathlib import Path\n\n\n"
                    "def test_hello_file():\n"
                    "    assert Path('hello.txt').read_text() == 'hello'\n"
                )

            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            result = harness.run_project(config(tmp), backend=backend)
            assert result["status"] == "done"

            worker_prompt = backend.call_history[0]["messages"][0]["content"]
            assert "RUN <program> [arguments...]" in worker_prompt
            assert "workspace is already the current directory" in worker_prompt
            assert "Do not use cd, &&, pipes,\nredirects, or other shell syntax" in worker_prompt
            assert "Output" not in worker_prompt
            assert "Inputs" not in worker_prompt
            assert "Accept" not in worker_prompt
            assert "SEARCH" not in worker_prompt

            run_results = [
                message["content"]
                for request in backend.call_history
                for message in request["messages"]
                if message["role"] == "user" and message["content"].startswith("command: python -B -m pytest -q")
            ]
            assert len(run_results) == 1
            assert "result: success" in run_results[0]
            assert "timed_out: no" in run_results[0]
            assert "exit_code: 0" in run_results[0]
            assert "stdout:" in run_results[0]

            with open(os.path.join(tmp, "hello.txt"), encoding="utf-8") as file:
                assert file.read() == "hello"
            assert vc.VersionControl(tmp).get_current() == "s1"

    def test_invalid_run_is_recoverable_then_valid_run_succeeds(self):
        init_backend = model.MockBackend([text_response(todo_item(1, "Verify argv-only RUN"))])
        backend = model.MockBackend([
            text_response("RUN cd somewhere && python -m pytest"),
            text_response("RUN python -B -m pytest -q"),
            text_response("WRITE test.txt\nverified\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Verify argv-only RUN", cfg, backend=init_backend)["status"] == "success"
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
            with open(os.path.join(tmp, "tests", "test_smoke.py"), "w", encoding="utf-8") as file:
                file.write("def test_smoke():\n    assert True\n")

            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"

            first_run_prompt = backend.call_history[1]["messages"][-1]["content"]
            assert "command: cd somewhere && python -m pytest" in first_run_prompt
            assert "result: error: command not found: cd" in first_run_prompt
            assert "stdout:" in first_run_prompt
            assert "stderr:" in first_run_prompt

            second_run_prompt = backend.call_history[2]["messages"][-1]["content"]
            assert "command: python -B -m pytest -q" in second_run_prompt
            assert "result: success" in second_run_prompt
            assert "exit_code: 0" in second_run_prompt

    def test_no_file_changes_reviewer_sees_only_diff(self):
        """Reviewer sees no file changes in diff — no RUN evidence in prompt."""
        step = {"n": 0}
        class DiffOnlyBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })
                prompt = messages[1]["content"] if len(messages) > 1 else ""
                if prompt.lstrip().startswith("Task T1:"):
                    step["n"] += 1
                    if step["n"] > 1:
                        raise RuntimeError("stop after first rework cycle")
                    return text_response("Done")
                if prompt.startswith("# Review Assignment"):
                    assert "(no file changes)" in prompt
                    assert "RUN evidence:" not in prompt
                    return text_response("REWORK\nReason: No code changes were made.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")
                raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Do nothing"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nDo nothing.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            result = harness.run_project(config(tmp), backend=DiffOnlyBackend())
            assert result["status"] == "error"

    def test_rework_reason_persists_across_restart(self):
        class FirstBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0

            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })

                prompt = messages[1]["content"] if len(messages) > 1 else ""
                if prompt.lstrip().startswith("Task T1:"):
                    if self.step == 0:
                        self.step = 1
                        return text_response("Done")
                    raise RuntimeError("stop after rework")
                if prompt.startswith("# Review Assignment"):
                    assert "RUN evidence:" not in prompt
                    return text_response("REWORK\nReason: Need evidence.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")
                raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        class SecondBackend(model.MockBackend):
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })

                prompt = messages[1]["content"] if len(messages) > 1 else ""
                if prompt.lstrip().startswith("Task T1:"):
                    assert "Previous reviewer feedback:" in prompt
                    assert "Need evidence." in prompt
                    return text_response("Done")
                if prompt.startswith("# Review Assignment"):
                    return text_response("ACCEPT\nReason: Fixed.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")
                raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as file:
                file.write("# Task\n\nMake no file changes; just verify the task flow.\n")
            with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
                file.write(todo_item(1, "Make no file changes; just verify the task flow"))
            with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as file:
                file.write("# Project Status\n\nInitialized.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as file:
                file.write("# Decisions\n\n")

            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            first = harness.run_project(config(tmp), backend=FirstBackend())
            assert first["status"] == "error"

            second_backend = SecondBackend()
            second = harness.run_project(config(tmp), backend=second_backend)
            assert second["status"] == "done"

            worker_prompts = [
                request["messages"][1]["content"]
                for request in second_backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].lstrip().startswith("Task T1:")
            ]
            assert len(worker_prompts) == 1
            assert "Previous reviewer feedback:" in worker_prompts[0]
            assert "Need evidence." in worker_prompts[0]

            with open(os.path.join(tmp, ".bid", "log.md"), encoding="utf-8") as file:
                log_text = file.read()
            assert "rework_reason: task=T1 base=s0 candidate=s1 reason=Need evidence." in log_text
            assert vc.VersionControl(tmp).get_current() == "s1"

    def test_malformed_write_is_rejected_and_logged(self):
        init_backend = model.MockBackend([text_response(todo_item(1, "Verify malformed write rejection"))])
        backend = model.MockBackend([
            text_response("WRITE README.md << 'EOF'\nboom\nEND WRITE"),
            text_response("WRITE notes.txt\nok\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fine."),
            text_response("COMPLETE\nReason: Done."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Verify malformed write rejection", cfg, backend=init_backend)["status"] == "success"

            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"

            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as file:
                assert file.read() == "ok"

            with open(os.path.join(tmp, ".bid", "log.md"), encoding="utf-8") as file:
                log_text = file.read()
            assert "worker raw response:" in log_text
            assert "worker parsed command:" in log_text
            assert "WRITE README.md << 'EOF'" in log_text
            assert "malformed WRITE path" in log_text

    def test_run_cannot_delete_control_state_and_worker_continues(self):
        init_backend = model.MockBackend([text_response(todo_item(1, "Protect control state"))])
        backend = model.MockBackend([
            text_response("RUN rm -f docs/todo.md\nDone"),
            text_response("WRITE notes.txt\nok\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Protect control state", cfg, backend=init_backend)["status"] == "success"

            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"

            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as file:
                todo_text = file.read()
            assert "Protect control state" in todo_text

            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as file:
                assert file.read() == "ok"

            with open(os.path.join(tmp, ".bid", "log.md"), encoding="utf-8") as file:
                log_text = file.read()
            assert "worker parsed command:\n  RUN rm -f docs/todo.md" in log_text
            assert "deletion denied: protected path docs/todo.md" in log_text

            assert any(
                "policy violation: protected deletion denied" in message.get("content", "")
                for request in backend.call_history
                for message in request["messages"]
                if message.get("role") == "user"
            )

    def test_unrecorded_run_exception_does_not_reuse_denied_deletion_evidence(self, monkeypatch):
        init_backend = model.MockBackend([text_response(todo_item(1, "Keep working after executor error"))])
        backend = model.MockBackend([
            text_response("RUN rm -f docs/todo.md"),
            text_response("RUN explode"),
            text_response("WRITE notes.txt\nok\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])

        original_run = adapter.WorkerAdapter._run_command

        def raise_before_recording(self, command):
            if command == "explode":
                raise RuntimeError("boom")
            return original_run(self, command)

        monkeypatch.setattr(adapter.WorkerAdapter, "_run_command", raise_before_recording)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Keep working after executor error", cfg, backend=init_backend)["status"] == "success"

            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"

            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as file:
                assert file.read() == "ok"

            assert "policy violation: protected deletion denied" in backend.call_history[1]["messages"][-1]["content"]
            assert "error: execution failed: boom" in backend.call_history[2]["messages"][-1]["content"]
            assert "protected deletion denied" not in backend.call_history[2]["messages"][-1]["content"]


class TestReworkFixedBase:
    """Verify beta-gate/rework-fixed-base: rollback after REWORK."""

    def test_rework_restores_workspace_byte_for_byte(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nmodified\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Wrong content."),
            text_response("WRITE notes.txt\nsecond attempt\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("ORIGINAL")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            with open(os.path.join(tmp, "notes.txt")) as f:
                assert f.read() == "second attempt"

    def test_rework_removes_shadow_file(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nmoved\nEND WRITE\nWRITE shadow.txt\nSHADOW\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Shadow file detected."),
            text_response("WRITE notes.txt\nclean\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("ORIGINAL")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            base_entries = set(os.listdir(tmp))
            harness.run_project(config(tmp), backend=backend)
            final_entries = set(os.listdir(tmp))
            # shadow.txt should not exist after REWORK restored to base
            assert "shadow.txt" not in final_entries

    def test_rework_preserves_todo(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nbad\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Wrong content."),
            text_response("WRITE notes.txt\ngood\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("ORIGINAL")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            with open(os.path.join(tmp, "docs", "todo.md")) as f:
                todo_text = f.read()
            assert "[x] T1" in todo_text

    def test_rework_worker_receives_feedback(self):
        class FeedbackCheckBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.seen_feedback = False
                self.review_count = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools, "max_tokens": max_tokens})
                prompt = messages[1]["content"] if len(messages) > 1 else ""
                if prompt.lstrip().startswith("Task T1:"):
                    if not self.seen_feedback:
                        self.seen_feedback = True
                        return text_response("WRITE notes.txt\nfirst\nEND WRITE\nDone")
                    else:
                        assert "Previous reviewer feedback:" in prompt, f"missing feedback in: {prompt[:200]}"
                        assert "Wrong content." in prompt
                        return text_response("WRITE notes.txt\nsecond\nEND WRITE\nDone")
                if prompt.startswith("# Review Assignment"):
                    self.review_count += 1
                    if self.review_count == 1:
                        return text_response("REWORK\nReason: Wrong content.")
                    return text_response("ACCEPT\nReason: Fixed.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")
                raise AssertionError(f"unexpected: {prompt[:80]}")
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            result = harness.run_project(config(tmp), backend=FeedbackCheckBackend())
            assert result["status"] == "done"

    def test_multiple_rework_both_restore_same_base(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nv1\nEND WRITE\nDone"),
            text_response("REWORK\nReason: v1 bad."),
            text_response("WRITE notes.txt\nv2\nEND WRITE\nDone"),
            text_response("REWORK\nReason: v2 bad."),
            text_response("WRITE notes.txt\nv3\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: v3 good."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("ORIGINAL")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            with open(os.path.join(tmp, "notes.txt")) as f:
                assert f.read() == "v3"

    def test_accept_does_not_restore_base(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit a.txt")),
            text_response("WRITE a.txt\nA-content\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Good."),
            text_response("WRITE b.txt\nB-content\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Good."),
            text_response("COMPLETE\nReason: All done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit a.txt") + todo_item(2, "Edit b.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit both.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            # Both files should exist and have the accepted content
            with open(os.path.join(tmp, "a.txt")) as f:
                assert f.read() == "A-content"
            with open(os.path.join(tmp, "b.txt")) as f:
                assert f.read() == "B-content"

    def test_rejected_candidate_preserved_in_vc(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nrejected draft\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Low quality."),
            text_response("WRITE notes.txt\naccepted draft\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Good."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            # Rejected candidate snapshot still exists and is byte-identical
            states_dir = os.path.join(tmp, ".bid", "states")
            s = sorted(os.listdir(states_dir))
            assert "s1" in s, f"rejected candidate state should exist in {s}"
            rejected_notes = open(os.path.join(states_dir, "s1", "notes.txt")).read().strip()
            assert rejected_notes == "rejected draft", \
                f"expected 'rejected draft', got {rejected_notes!r}"
            # Active workspace has the accepted content
            ws_notes = open(os.path.join(tmp, "notes.txt")).read().strip()
            assert ws_notes == "accepted draft", f"expected 'accepted draft', got {ws_notes!r}"
            # Log has structured rework provenance
            log_text = open(os.path.join(tmp, ".bid", "log.md")).read()
            assert "task=T1 base=s0 candidate=s1 reason=Low quality." in log_text

    def test_multiple_rejected_candidates_independently_preserved(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Edit notes.txt")),
            text_response("WRITE notes.txt\nv1\nEND WRITE\nDone"),
            text_response("REWORK\nReason: v1 bad."),
            text_response("WRITE notes.txt\nv2\nEND WRITE\nDone"),
            text_response("REWORK\nReason: v2 bad."),
            text_response("WRITE notes.txt\nv3\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: v3 good."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Edit notes.txt"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nEdit notes.txt.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            with open(os.path.join(tmp, "notes.txt"), "w") as f:
                f.write("ORIGINAL")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_project(config(tmp), backend=backend)
            states_dir = os.path.join(tmp, ".bid", "states")
            s = sorted(os.listdir(states_dir))
            assert "s1" in s, f"rejected attempt 1 should exist in {s}"
            assert "s2" in s, f"rejected attempt 2 should exist in {s}"
            assert "s3" in s, f"accepted attempt should exist in {s}"
            # Each attempt has its own content
            assert open(os.path.join(states_dir, "s1", "notes.txt")).read().strip() == "v1"
            assert open(os.path.join(states_dir, "s2", "notes.txt")).read().strip() == "v2"
            assert open(os.path.join(states_dir, "s3", "notes.txt")).read().strip() == "v3"
            # Active workspace has the accepted content
            assert open(os.path.join(tmp, "notes.txt")).read().strip() == "v3"
            # Log has structured provenance for each rework
            log_text = open(os.path.join(tmp, ".bid", "log.md")).read()
            assert "base=s0 candidate=s1 reason=v1 bad." in log_text
            assert "base=s0 candidate=s2 reason=v2 bad." in log_text


class TestContextBoundary:
    """Context isolation: each role sees only its authoritative state."""

    def test_stale_worker_run_excluded_from_reviewer(self):
        """RUN output from before the final WRITE does not reach the Reviewer."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Create file"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nCreate a file and verify it does not exist yet.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            RUN_SENTINEL = "STALE_RUN_OUTPUT_THIS_SHOULD_NOT_APPEAR"
            class StaleRunBackend(model.MockBackend):
                def __init__(self):
                    super().__init__([])
                    self.phase = 0
                def run(self, messages, tools, max_tokens=None):
                    self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    if prompt.lstrip().startswith("Task T1:"):
                        self.phase += 1
                        if self.phase == 1:
                            return text_response(
                                f"RUN echo {RUN_SENTINEL}\n"
                                "WRITE output.txt\ncreated\nEND WRITE\n"
                                "Done"
                            )
                        raise RuntimeError("stop after one attempt")
                    if prompt.startswith("# Review Assignment"):
                        prompt_text = messages[1]["content"]
                        assert RUN_SENTINEL not in prompt_text, \
                            f"Stale RUN output leaked into reviewer prompt: {RUN_SENTINEL}"
                        return text_response("REWORK\nReason: Need more work.")
                    raise AssertionError(f"unexpected: {prompt[:80]}")

            result = harness.run_project(config(tmp), backend=StaleRunBackend())
            assert result["status"] == "error"

    def test_reviewer_prompt_includes_identity_and_verification_status(self):
        """Reviewer prompt includes state IDs and explicitly states no verification."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            os.makedirs(os.path.join(tmp, ".bid", "states", "s1", "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, ".bid", "current"), "w") as f:
                f.write("s1\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", ".bid"), "w") as f:
                f.write("")

            review = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s1", candidate_state="s2")
            captured = {}
            class CheckBackend:
                def run(self, messages, tools, max_tokens=None):
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    captured["prompt"] = prompt
                    return {"role": "assistant", "content": "ACCEPT\nReason: OK.", "finish_reason": "stop"}
            review.run(CheckBackend())
            prompt = captured["prompt"]
            assert "Fixed base: s1" in prompt
            assert "Submitted candidate: s2" in prompt
            assert "No harness-owned verification was executed" in prompt
            assert "satisfies the stated current task" in prompt
            assert "Unfinished later checklist tasks are not grounds for REWORK" in prompt
            assert "not require production-source modifications" in prompt
            assert "RUN evidence:" not in prompt

    def test_worker_receives_only_normalized_feedback(self):
        """REWORK Worker sees only the reviewer reason, not provenance."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test feedback"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest feedback.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            class FeedbackCheckBackend(model.MockBackend):
                def __init__(self):
                    super().__init__([])
                    self.worker_call = 0
                def run(self, messages, tools, max_tokens=None):
                    self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    if prompt.lstrip().startswith("Task T1:"):
                        self.worker_call += 1
                        if self.worker_call == 2:
                            assert "Previous reviewer feedback:" in prompt
                            assert "Bad result" in prompt
                            assert "task=T1" not in prompt, "provenance must not leak"
                            assert "base=" not in prompt, "provenance must not leak"
                            assert "candidate=" not in prompt, "provenance must not leak"
                        return text_response("Done")
                    if prompt.startswith("# Review Assignment"):
                        if self.worker_call == 1:
                            return text_response("REWORK\nReason: Bad result.")
                        return text_response("ACCEPT\nReason: Fixed.")
                    if prompt.startswith("# Completion Review"):
                        return text_response("COMPLETE\nReason: Done.")
                    raise AssertionError(f"unexpected: {prompt[:80]}")

            result = harness.run_project(config(tmp), backend=FeedbackCheckBackend())
            assert result["status"] == "done"

    def test_task_reviewer_scope_is_current_task_only(self):
        """Task Reviewer prompt clearly separates current task from the checklist."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            os.makedirs(os.path.join(tmp, ".bid", "states", "s1", "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Change solver\n- [ ] T2 — Update callers\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nChange solver and update callers.\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Change solver\n- [ ] T2 — Update callers\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "task.md"), "w") as f:
                f.write("# Task\n\nChange solver and update callers.\n")
            with open(os.path.join(tmp, ".bid", "current"), "w") as f:
                f.write("s1\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", ".bid"), "w") as f:
                f.write("")

            review = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s1")
            seen = {}
            class CheckBackend:
                def run(self, messages, tools, max_tokens=None):
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    seen["prompt"] = prompt
                    return {"role": "assistant", "content": "ACCEPT\nReason: Solver changed.", "finish_reason": "stop"}
            review.run(CheckBackend())
            prompt = seen.get("prompt", "")
            assert "Task:\nChange solver" in prompt, f"prompt should describe T1 task: {prompt[:200]}"

    def test_completion_reviewer_no_attempt_history(self):
        """Completion Reviewer does not receive Worker attempt transcripts."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Complete task"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nComplete task.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            class CompletionOnlyBackend(model.MockBackend):
                def __init__(self):
                    super().__init__([])
                    self.worker_call = 0
                def run(self, messages, tools, max_tokens=None):
                    self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    if prompt.lstrip().startswith("Task T1:"):
                        self.worker_call += 1
                        if self.worker_call == 1:
                            return text_response("RUN echo REJECTED_HISTORY\nWRITE output.txt\nfail\nEND WRITE\nDone")
                        return text_response("WRITE output.txt\npass\nEND WRITE\nDone")
                    if prompt.startswith("# Review Assignment"):
                        if self.worker_call == 1:
                            return text_response("REWORK\nReason: Wrong content.")
                        return text_response("ACCEPT\nReason: Fixed.")
                    if prompt.startswith("# Completion Review"):
                        assert "RUN evidence:" not in prompt
                        assert "REWORK" not in prompt
                        assert "REJECTED_HISTORY" not in prompt
                        assert "[x] T1" in prompt
                        return text_response("COMPLETE\nReason: All done.")
                    raise AssertionError(f"unexpected: {prompt[:80]}")

            result = harness.run_project(config(tmp), backend=CompletionOnlyBackend())
            assert result["status"] == "done"

    def test_logs_retain_full_history(self):
        """Removing from prompts does not remove from forensic logs."""
        backend = model.MockBackend([
            text_response(todo_item(1, "Run tests")),
            text_response("RUN python -B -m pytest -q\nDone"),
            text_response("REWORK\nReason: Tests needed."),
            text_response("WRITE test.txt\npass\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Run tests"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nRun tests.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            result = harness.run_project(config(tmp), backend=backend)
            assert result["status"] == "done"
            log_text = open(os.path.join(tmp, ".bid", "log.md")).read()
            assert "RUN python -B -m pytest -q" in log_text
            assert "WRITE test.txt" in log_text
            assert "rework_reason:" in log_text


class TestAcceptanceContract:
    """Task Reviewer must not invent requirements beyond the stated task."""

    def test_prompt_contains_no_source_edit_rule(self):
        """Reviewer prompt instructs not to require production-source changes."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            os.makedirs(os.path.join(tmp, ".bid", "states", "s1", "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, ".bid", "current"), "w") as f:
                f.write("s1\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", ".bid"), "w") as f:
                f.write("")

            review = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s1")
            captured = {}
            class CheckBackend:
                def run(self, messages, tools, max_tokens=None):
                    prompt = messages[1]["content"] if len(messages) > 1 else ""
                    captured["prompt"] = prompt
                    return {"role": "assistant", "content": "ACCEPT\nReason: OK.", "finish_reason": "stop"}
            review.run(CheckBackend())
            prompt = captured["prompt"]
            assert "not require production-source modifications" in prompt
            assert "Return REWORK only for a concrete unmet task requirement" in prompt

    def test_test_only_candidate_not_rejected_for_missing_source(self):
        """A test-only candidate should not be rejected merely because production code is unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            os.makedirs(os.path.join(tmp, ".bid", "states", "s1", "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Verify return type is ndarray\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nVerify return type is ndarray.\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Verify return type is ndarray\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "task.md"), "w") as f:
                f.write("# Task\n\nVerify return type is ndarray.\n")
            with open(os.path.join(tmp, ".bid", "current"), "w") as f:
                f.write("s1\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", ".bid"), "w") as f:
                f.write("")
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)
            with open(os.path.join(tmp, "tests", "test_return_type.py"), "w") as f:
                f.write("import numpy as np\nfrom argus.solve import solve_point\n\ndef test_returns_ndarray():\n    result = solve_point(np.array([0.001, 0.0011, 0.0012, 0.0013]))\n    assert isinstance(result, np.ndarray)\n")

            # Run Reviewer — should not reject for missing production changes
            review = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s1")
            captured = {}
            class DummyBackend:
                def run(self, messages, tools, max_tokens=None):
                    captured["prompt"] = messages[1]["content"] if len(messages) > 1 else ""
                    return {"role": "assistant", "content": "ACCEPT\nReason: Test verifies ndarray return type.", "finish_reason": "stop"}
            result = review.run(DummyBackend())
            assert result["verdict"] == "ACCEPT", f"Reviewer rejected test-only candidate: {result}"

    def test_reviewer_may_still_reject_defective_test(self):
        """Reviewer may reject a test-only candidate if the test itself is defective."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            os.makedirs(os.path.join(tmp, ".bid", "states", "s1", "docs"))
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "todo.md"), "w") as f:
                f.write("- [ ] T1 — Test\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, ".bid", "current"), "w") as f:
                f.write("s1\n")
            with open(os.path.join(tmp, ".bid", "states", "s1", ".bid"), "w") as f:
                f.write("")

            review = adapter.TaskReviewAdapter(config(tmp), 1, base_state="s1")
            class RejectBackend:
                def run(self, messages, tools, max_tokens=None):
                    return {"role": "assistant", "content": "REWORK\nReason: The added test file imports a module that does not exist in the workspace (src.fake). A test that cannot be collected is not a valid completion.", "finish_reason": "stop"}
            result = review.run(RejectBackend())
            assert result["verdict"] == "REWORK", "Reviewer should reject defective tests"


class TestRespawnRollback:
    """Stalled/timeout Worker attempts are aborted transactions: workspace restored."""

    def test_stalled_modifications_removed(self):
        """File written by a stalled Worker is removed by rollback."""
        class StallAfterWrite(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                self.step += 1
                if self.step == 1:
                    return text_response("WRITE f.txt\nstale\nEND WRITE")
                return text_response("READ noexist.txt")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test stall rollback"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            cfg = config(tmp, repeat_action_limit=1)
            result = harness.run_worker_session(1, cfg, backend=StallAfterWrite())
            assert result["status"] == "stalled", f"expected stalled, got {result}"
            assert not os.path.exists(os.path.join(tmp, "f.txt")), "stalled Worker writes must be removed"

    def test_timeout_modifications_removed(self):
        """File written by a timed-out Worker is removed by rollback."""
        class TimeoutAfterWrite(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                self.step += 1
                if self.step == 1:
                    return text_response("WRITE f.txt\nstale\nEND WRITE")
                return text_response("READ noexist.txt")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test timeout rollback"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            cfg = config(tmp, worker_timeout=2, inactivity_timeout=1, repeat_action_limit=10000)
            result = harness.run_worker_session(1, cfg, backend=TimeoutAfterWrite())
            assert result["status"] == "timeout", f"expected timeout, got {result}"
            assert not os.path.exists(os.path.join(tmp, "f.txt")), "timed-out Worker writes must be removed"

    def test_todo_preserved_after_respawn_rollback(self):
        """TODO.md survives workspace restoration after stall."""
        class StallBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                self.step += 1
                if self.step == 1:
                    return text_response("WRITE f.txt\nx\nEND WRITE")
                return text_response("READ noexist.txt")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test todo preservation"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            cfg = config(tmp, repeat_action_limit=1)
            harness.run_worker_session(1, cfg, backend=StallBackend())
            with open(os.path.join(tmp, "docs", "todo.md")) as f:
                assert "Test todo preservation" in f.read()

    def test_no_candidate_state_created_for_stalled(self):
        """Stalled Worker produces no saved VC state."""
        class StallBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                self.step += 1
                if self.step == 1:
                    return text_response("WRITE f.txt\nx\nEND WRITE")
                return text_response("READ noexist.txt")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test no state on stall"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            cfg = config(tmp, repeat_action_limit=1)
            states_before = set(os.listdir(os.path.join(tmp, ".bid", "states")))
            harness.run_worker_session(1, cfg, backend=StallBackend())
            states_after = set(os.listdir(os.path.join(tmp, ".bid", "states")))
            assert states_before == states_after, "stalled Worker must not create a candidate state"

    def test_multiple_respawns_same_fixed_base(self):
        """Each respawn restores the same fixed base."""
        class MultiStallBackend(model.MockBackend):
            def __init__(self):
                super().__init__([])
                self.step = 0
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({"messages": [dict(m) for m in messages], "tools": tools})
                self.step += 1
                return text_response("WRITE f.txt\nstale\nEND WRITE") if self.step == 1 else text_response("READ noexist.txt")

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "Test multi-stall"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nTest.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            cfg = config(tmp, repeat_action_limit=1)
            for attempt in range(3):
                harness.run_worker_session(1, cfg, backend=MultiStallBackend())
                assert not os.path.exists(os.path.join(tmp, "f.txt")), f"stale file survived attempt {attempt}"

    def test_normal_submission_accept_and_rework_unchanged(self):
        """Normal flow is unaffected by respawn rollback changes."""
        backend = model.MockBackend([
            text_response(todo_item(1, "Write result")),
            text_response("WRITE notes.txt\ndraft\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Draft too weak."),
            text_response("WRITE notes.txt\nfinal\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Write result", cfg, backend=backend)["status"] == "success"
            with open(os.path.join(tmp, "notes.txt"), "w", encoding="utf-8") as f:
                f.write("BASE_SENTINEL")
            vc.VersionControl(tmp).save_state("prep", "seed sentinel")
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done", f"expected done, got {result}"
            with open(os.path.join(tmp, "notes.txt")) as f:
                assert f.read() == "final"


class TestTimingObservability:
    """Structured event log: order, durations, usage, behavior unchanged."""

    def _events(self, tmp):
        path = os.path.join(os.path.dirname(tmp), os.path.basename(tmp) + "-events", "events.jsonl")
        if not os.path.exists(path):
            return []
        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def test_event_log_created_append_only(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "No-op")),
            text_response("Done"),
            text_response("ACCEPT\nReason: OK."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("No-op", cfg, backend=backend)["status"] == "success"
            harness.run_project(cfg, backend=backend)
            events = self._events(tmp)
            assert len(events) > 0, "event log should exist"
            for ev in events:
                assert "ts_utc" in ev, f"missing ts_utc: {ev}"
                assert "elapsed_s" in ev, f"missing elapsed_s: {ev}"
            kinds = {ev["event"] for ev in events}
            assert "worker_session" in kinds
            assert "model_request" in kinds

    def test_event_order(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Do work")),
            text_response("WRITE f.txt\nx\nEND WRITE\nRUN echo hi\nDone"),
            text_response("ACCEPT\nReason: OK."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Do work", cfg, backend=backend)["status"] == "success"
            harness.run_project(cfg, backend=backend)
            events = self._events(tmp)
            # find worker_session start, model_request, command, worker_session end
            sess_starts = [i for i, e in enumerate(events) if e["event"] == "worker_session" and e.get("phase") == "start"]
            sess_ends = [i for i, e in enumerate(events) if e["event"] == "worker_session" and e.get("phase") == "end"]
            assert sess_starts and sess_ends, "worker_session start/end present"
            assert sess_starts[0] < sess_ends[-1], "session start precedes end"
            cmd_idxs = [i for i, e in enumerate(events) if e["event"] == "command"]
            assert cmd_idxs, "command events present"
            # command events occur between session start and end
            assert sess_starts[0] < cmd_idxs[0] < sess_ends[-1]

    def test_duration_fields_present(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Do work")),
            text_response("RUN echo hi\nDone"),
            text_response("ACCEPT\nReason: OK."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Do work", cfg, backend=backend)["status"] == "success"
            harness.run_project(cfg, backend=backend)
            events = self._events(tmp)
            end_events = [e for e in events if e.get("phase") == "end"]
            assert end_events, "end-phase events present"
            for e in end_events:
                assert "duration_s" in e, f"missing duration_s: {e}"
                assert isinstance(e["duration_s"], (int, float))
                assert e["duration_s"] >= 0
            run_cmds = [e for e in events if e["event"] == "run_command"]
            assert run_cmds, "run_command events present"
            assert "exit_code" in run_cmds[0]

    def test_usage_preserved(self):
        class UsageBackend(model.MockBackend):
            def run(self, messages, tools, max_tokens=None):
                self.call_history.append({
                    "messages": [dict(message) for message in messages],
                    "tools": tools,
                    "max_tokens": max_tokens,
                })
                response = text_response("Done")
                response["usage"] = {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}
                return response

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"))
            with open(os.path.join(tmp, "docs", "todo.md"), "w") as f:
                f.write(todo_item(1, "No-op"))
            with open(os.path.join(tmp, "docs", "task.md"), "w") as f:
                f.write("# Task\n\nNo-op.\n")
            with open(os.path.join(tmp, "docs", "project-status.md"), "w") as f:
                f.write("# Project Status\n\nInit.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w") as f:
                f.write("# Decisions\n\n")
            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()
            harness.run_worker_session(1, config(tmp), backend=UsageBackend())
            events = self._events(tmp)
            worker_reqs = [e for e in events if e["event"] == "model_request" and e.get("role") == "worker" and e.get("phase") == "end"]
            assert worker_reqs, "worker model_request end events present"
            assert worker_reqs[0]["prompt_tokens"] == 111
            assert worker_reqs[0]["completion_tokens"] == 22
            assert worker_reqs[0]["total_tokens"] == 133

    def test_execution_behavior_unchanged(self):
        backend = model.MockBackend([
            text_response(todo_item(1, "Write result")),
            text_response("WRITE notes.txt\ndraft\nEND WRITE\nDone"),
            text_response("REWORK\nReason: Draft too weak."),
            text_response("WRITE notes.txt\nfinal\nEND WRITE\nDone"),
            text_response("ACCEPT\nReason: Fixed."),
            text_response("COMPLETE\nReason: Done."),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config(tmp)
            assert harness.init_project("Write result", cfg, backend=backend)["status"] == "success"
            result = harness.run_project(cfg, backend=backend)
            assert result["status"] == "done"
            with open(os.path.join(tmp, "notes.txt"), encoding="utf-8") as f:
                assert f.read() == "final"
            with open(os.path.join(tmp, "docs", "todo.md"), encoding="utf-8") as f:
                assert "[x] T1" in f.read()


class TestReviewDiffCoverage:
    def test_large_early_diff_keeps_later_file_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base")
            candidate = os.path.join(tmp, "candidate")
            for root in (base, candidate):
                os.makedirs(os.path.join(root, "argus"))
                os.makedirs(os.path.join(root, "tests"))
            files = {
                "a_HEAD_SENTINEL_large.py": ("before\n", "A" * 4000 + "\nTAIL_SENTINEL\n"),
                "argus/solve.py": ("old solve\n", "def solve_point():\n    return None\n"),
                "argus/sensitivity.py": ("old sensitivity\n", "new sensitivity\n"),
                "tests/test_solve_point_fail_closed.py": ("old test\n", "def test_fail_closed():\n    TEST_DETAIL_SENTINEL\n"),
            }
            for rel, (before, after) in files.items():
                for root, content in ((base, before), (candidate, after)):
                    with open(os.path.join(root, rel), "w", encoding="utf-8") as file:
                        file.write(content)

            diff = adapter._workspace_diff(base, candidate, limit=800)

            assert len(diff) <= 800
            for rel in files:
                assert f"- modified {rel}" in diff
                assert f"### modified {rel}" in diff
            assert "return None" in diff
            assert "TEST_DETAIL_SENTINEL" in diff
            large_section = diff.split("### modified a_HEAD_SENTINEL_large.py", 1)[1]
            assert large_section.index("HEAD_SENTINEL") < large_section.index("...[middle truncated for this file]...") < large_section.index("TAIL_SENTINEL")

    def test_small_multi_file_diff_is_complete_without_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "base")
            candidate = os.path.join(tmp, "candidate")
            for root in (base, candidate):
                os.makedirs(os.path.join(root, "argus"))
            with open(os.path.join(base, "argus", "solve.py"), "w", encoding="utf-8") as file:
                file.write("old\n")
            with open(os.path.join(candidate, "argus", "solve.py"), "w", encoding="utf-8") as file:
                file.write("new\n")
            with open(os.path.join(candidate, "added.py"), "w", encoding="utf-8") as file:
                file.write("added\n")

            diff = adapter._workspace_diff(base, candidate)

            assert "- added added.py" in diff
            assert "- modified argus/solve.py" in diff
            assert "added" in diff
            assert "-old" in diff
            assert "+new" in diff
            assert "...[middle truncated for this file]..." not in diff

    def test_task_reviewer_prompt_includes_late_changed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Review fail-closed solver"))
            for rel, content in {
                "a_large.py": "before\n",
                "argus/solve.py": "old solve\n",
                "argus/sensitivity.py": "old sensitivity\n",
                "tests/test_solve_point_fail_closed.py": "old test\n",
            }.items():
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as file:
                    file.write(content)
            base_state = vc.VersionControl(tmp).save_state("seed", "causal shape")
            for rel, content in {
                "a_large.py": "A" * 20000,
                "argus/solve.py": "def solve_point():\n    return None\n",
                "argus/sensitivity.py": "new sensitivity\n",
                "tests/test_solve_point_fail_closed.py": "def test_fail_closed():\n    assert True\n",
            }.items():
                with open(os.path.join(tmp, rel), "w", encoding="utf-8") as file:
                    file.write(content)

            backend = model.MockBackend([text_response("REWORK\nReason: Continue.")])
            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state=base_state).run(backend)
            prompt = backend.call_history[0]["messages"][1]["content"]

            assert result["verdict"] == "REWORK"
            for rel in ("a_large.py", "argus/solve.py", "argus/sensitivity.py", "tests/test_solve_point_fail_closed.py"):
                assert f"- modified {rel}" in prompt
                assert f"### modified {rel}" in prompt
            assert "return None" in prompt
            assert "test_fail_closed" in prompt
