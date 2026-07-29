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
            text_response("Done"),
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

            review_prompt = next(
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            )
            assert "RUN evidence:" in review_prompt
            assert "command: cd somewhere && python -m pytest" in review_prompt
            assert "command: python -B -m pytest -q" in review_prompt

    def test_no_file_changes_can_pass_on_run_evidence(self):
        class EvidenceBackend(model.MockBackend):
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
                    return text_response("RUN python -B -m pytest -q\nDone")
                if prompt.startswith("# Review Assignment"):
                    assert "(no file changes)" in prompt
                    assert "RUN evidence:" in prompt
                    assert "exit_code: 0" in prompt
                    return text_response("ACCEPT\nReason: Successful RUN evidence is enough.")
                if prompt.startswith("# Completion Review"):
                    return text_response("COMPLETE\nReason: Done.")
                raise AssertionError(f"unexpected prompt: {prompt[:80]}")

        backend = EvidenceBackend()

        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "tests"), exist_ok=True)

            with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as file:
                file.write("# Task\n\nVerify the workspace with pytest, without changing files.\n")
            with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
                file.write(todo_item(1, "Verify the workspace with pytest, without changing files"))
            with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as file:
                file.write("# Project Status\n\nInitialized.\n")
            with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as file:
                file.write("# Decisions\n\n")
            with open(os.path.join(tmp, "tests", "test_smoke.py"), "w", encoding="utf-8") as file:
                file.write("def test_smoke():\n    assert True\n")

            harness.ensure_workspace(tmp)
            vc.VersionControl(tmp).init()

            result = harness.run_project(config(tmp), backend=backend)
            assert result["status"] == "done"

            review_prompt = next(
                request["messages"][1]["content"]
                for request in backend.call_history
                if len(request["messages"]) > 1 and request["messages"][1]["content"].startswith("# Review Assignment")
            )
            assert "RUN evidence:" in review_prompt
            assert "exit_code: 0" in review_prompt

            with open(os.path.join(tmp, ".bid", "log.md"), encoding="utf-8") as file:
                log_text = file.read()
            assert "RUN evidence:" in log_text
            assert "command: python -B -m pytest -q" in log_text
            assert "exit_code: 0" in log_text
            assert vc.VersionControl(tmp).get_current() == "s1"

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
            latest_s2 = log_text.rsplit("### s2\n", 1)[1]
            assert "Need evidence." not in latest_s2
            assert "rework_reason:" in latest_s2
            assert vc.VersionControl(tmp).get_current() == "s2"

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
