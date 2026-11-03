import tempfile
import os

from bid import adapter, harness, model, todo, vc


def text_response(text):
    return {"role": "assistant", "content": text, "tool_calls": None, "finish_reason": "stop"}


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


def prepare_workspace(tmp, todo_text, task_md="# Task\n\nFix ARGUS point reconstruction so solve_point raises argus.solve.PhysicallyInvalidError when no physically valid beam-constrained solution exists. Valid reconstructions must continue returning the existing ndarray result. Update every affected production caller to handle PhysicallyInvalidError explicitly while preserving its existing public return contract. Add regressions for the all-invalid case and affected caller behavior. Run focused tests and the complete ARGUS test suite. Make no unrelated refactors."):
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as f:
        f.write(todo_text)
    with open(os.path.join(tmp, "docs", "task.md"), "w", encoding="utf-8") as f:
        f.write(task_md)
    with open(os.path.join(tmp, "docs", "project-status.md"), "w", encoding="utf-8") as f:
        f.write("# Project Status\n\nInitialized.\n")
    with open(os.path.join(tmp, "docs", "decisions.md"), "w", encoding="utf-8") as f:
        f.write("# Decisions\n\n")
    harness.ensure_workspace(tmp)
    vc.VersionControl(tmp).init()


def todo_item(n, desc):
    return f"- [ ] T{n} — {desc}\n"


class TestReviewerHardScope:
    def test_hard_scope_labels_and_out_of_scope(self):
        todo = todo_item(1, "Modify argus.solve") + todo_item(2, "Add regression") + todo_item(3, "Update callers")
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo)
            vc.VersionControl(tmp).save_state("prep", "seed")
            backend = model.MockBackend([text_response("ACCEPT\nReason: ok")])
            # capture prompt
            orig_backend = backend
            captured = {}
            orig_run = backend.run
            def capture(messages, tools, max_tokens=None):
                captured["prompt"] = messages[1]["content"] if len(messages) > 1 else ""
                return orig_run(messages, tools, max_tokens)
            backend.run = capture
            adapter.TaskReviewAdapter(config(tmp), 1, base_state="s0", candidate_state="s1").run(backend)
            prompt = captured["prompt"]
            assert "## BACKGROUND ONLY — Original request" in prompt
            assert "## CURRENT TASK — Acceptance boundary" in prompt
            assert "CRITICAL SCOPE RULE" in prompt
            assert "MUST NOT cause REWORK" in prompt
            assert "Global constraints that remain binding" in prompt
            assert "no unrelated refactors" in prompt
            assert "Unfinished later checklist tasks are not grounds for REWORK" in prompt
            # later tasks listed as out of scope
            assert "T2 Add regression" in prompt
            assert "T3 Update callers" in prompt
            assert "OUT OF SCOPE" in prompt

    def test_correct_bounded_t1_with_future_absent_accept(self):
        # T1 minimal correct: add exceptions.py + solve.py raise, no T2/T3
        todo = todo_item(1, "Modify argus.solve") + todo_item(2, "Add regression") + todo_item(3, "Update callers")
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo)
            vc_sys = vc.VersionControl(tmp)
            base = vc_sys.get_current()
            os.makedirs(os.path.join(tmp, "argus"), exist_ok=True)
            with open(os.path.join(tmp, "argus/solve.py"), "w") as f:
                f.write("raise PhysicallyInvalidError")
            cand = vc_sys.save_state("candidate", "T1 correct minimal")
            backend = model.MockBackend([text_response("ACCEPT\nReason: T1 solve change complete, T2/T3 out of scope")])
            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state=base, candidate_state=cand).run(backend)
            assert result["verdict"] == "ACCEPT"
            # Ensure prompt for this T1 did not require T2/T3
            prompt = backend.call_history[0]["messages"][1]["content"]
            assert "T2 Add regression" in prompt  # out of scope listed
            assert "CRITICAL SCOPE RULE" in prompt

    def test_incomplete_t1_rework(self):
        todo = todo_item(1, "Modify argus.solve") + todo_item(2, "Add regression") + todo_item(3, "Update callers")
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo)
            vc_sys = vc.VersionControl(tmp)
            base = vc_sys.get_current()
            # incomplete candidate: no file change (use base as candidate)
            # Create empty diff candidate by saving same state? Use base as candidate via same dir
            # Instead, save a state with no meaningful change (still s1)
            cand = base  # empty diff
            # Use mock that returns REWORK for genuine T1 defect
            backend = model.MockBackend([text_response("REWORK\nReason: solve_point still uses fallback starts[0], does not raise PhysicallyInvalidError")])
            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state=base, candidate_state=cand).run(backend)
            assert result["verdict"] == "REWORK"
            assert "fallback" in result["reason"] or "PhysicallyInvalidError" in result["reason"]

    def test_global_constraint_violation_still_rework(self):
        todo = todo_item(1, "Modify argus.solve") + todo_item(2, "Add regression")
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo)
            vc_sys = vc.VersionControl(tmp)
            base = vc_sys.get_current()
            os.makedirs(os.path.join(tmp, "argus"), exist_ok=True)
            with open(os.path.join(tmp, "argus/reflectors.py"), "w") as f:
                f.write("unrelated")
            with open(os.path.join(tmp, "argus/solve.py"), "w") as f:
                f.write("raise PhysicallyInvalidError")
            cand = vc_sys.save_state("candidate", "T1 with unrelated")
            backend = model.MockBackend([text_response("REWORK\nReason: unrelated refactor reflectors.py violates no unrelated refactors")])
            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state=base, candidate_state=cand).run(backend)
            assert result["verdict"] == "REWORK"
            assert "unrelated" in result["reason"].lower()

    def test_state_aware_candidate_review_unchanged(self):
        # Existing state-aware behavior: diff from base snapshot vs candidate snapshot, not live workspace
        with tempfile.TemporaryDirectory() as tmp:
            prepare_workspace(tmp, todo_item(1, "Verify the ready solution"))
            with open(os.path.join(tmp, "solution.py"), "w", encoding="utf-8") as f:
                f.write("ready\n")
            vc_sys = vc.VersionControl(tmp)
            base = vc_sys.save_state("seed", "ready")
            cand = vc_sys.save_state("worker", "no changes")
            # dirty live workspace should not be reviewed
            with open(os.path.join(tmp, "solution.py"), "w", encoding="utf-8") as f:
                f.write("live workspace must not be reviewed\n")
            backend = model.MockBackend([text_response("ACCEPT\nReason: ok")])
            result = adapter.TaskReviewAdapter(config(tmp), 1, base_state=base, candidate_state=cand).run(backend)
            assert result["verdict"] == "ACCEPT"
            prompt = backend.call_history[0]["messages"][1]["content"]
            assert "Candidate final state (bounded snapshot view):" in prompt
            assert "### solution.py" in prompt
            assert "live workspace must not be reviewed" not in prompt
