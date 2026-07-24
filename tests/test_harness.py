import os
import tempfile

from bid import adapter, harness, model, vc


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


class TestWorkerSession:
    def test_worker_can_finish_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "No-op task"))
            backend = model.MockBackend([text_response("Done")])
            result = harness.run_worker_session(1, config(tmp), backend=backend)
            assert result["status"] == "submitted"
            assert result["termination"] == "normal"
            assert vc.VersionControl(tmp).get_current() == "s1"


class TestRunProject:
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
