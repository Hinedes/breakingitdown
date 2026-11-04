import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import runner
from bid.model import MockBackend


def _repo(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    runner._git(["init", "--quiet"], cwd=source)
    (source / "core.py").write_text("value = 1\n", encoding="utf-8")
    runner._git(["add", "."], cwd=source)
    runner._git(["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "base"], cwd=source)
    base = runner._git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
    return source, base


def test_runner_self_test(tmp_path):
    runner.self_test()


def test_bid_init_preserves_the_base_repo_and_removes_backup(tmp_path):
    source, base = _repo(tmp_path)
    checkout = runner.clone_checkout(source, base, tmp_path / "run" / "repo")
    backend = MockBackend([
        {"role": "assistant", "content": "- [ ] T1 — Create hello.py", "finish_reason": "stop"}
    ])
    config = runner.bid_config(checkout, "http://127.0.0.1:1/v1/chat/completions", "mock")
    from bid import harness

    result = harness.init_project("Create hello.py", config, backend)
    assert result["status"] == "success"
    assert (checkout / "core.py").read_text() == "value = 1\n"
    assert runner._git(["rev-parse", "HEAD"], cwd=checkout).stdout.strip() == base
    assert (checkout / ".git").is_dir()
    assert (checkout / "docs" / "todo.md").read_text(encoding="utf-8").strip() == "- [ ] T1 — Create hello.py"
    assert not (checkout.parent / ".bid_backup").exists()


def test_bid_full_mock_flow_preserves_exact_issue_and_patch(tmp_path):
    source, base = _repo(tmp_path)
    checkout = runner.clone_checkout(source, base, tmp_path / "bidrun" / "repo")
    row = {"instance_id": "repo__project-1", "repo": "repo/project", "base_commit": base,
           "problem_statement": "Fix core.py\n\n```python\nvalue = 2\n```"}
    backend = MockBackend([
        {"role": "assistant", "content": "- [ ] T1 — Update core.py", "finish_reason": "stop"},
        {"role": "assistant", "content": "WRITE core.py\nvalue = 2\nEND WRITE\nDone", "finish_reason": "stop"},
        {"role": "assistant", "content": "ACCEPT\nReason: core.py changed", "finish_reason": "stop"},
        {"role": "assistant", "content": "# Done\n- T1\n# Project\nCOMPLETE\n", "finish_reason": "stop"},
    ])
    config = runner.bid_config(checkout, "http://127.0.0.1:1/v1/chat/completions", "mock",
                               seed=7, task_statement=row["problem_statement"])
    patch = runner.run_bid(checkout, row, config, backend,
                           extract_patch_fn=lambda: runner.extract_patch(checkout, base))
    assert "core.py" in patch and "value = 2" in patch
    assert runner._git(["rev-parse", "HEAD"], cwd=checkout).stdout.strip() == base
    worker_messages = backend.call_history[1]["messages"]
    assert any("value = 2" in message.get("content", "") for message in worker_messages)
    assert any("Full public issue statement" in message.get("content", "") for message in worker_messages)
    assert backend.call_history[1]["messages"][0]["content"]
    assert backend.call_history[1].get("grammar") is None


def test_cli_exits_nonzero_on_declared_harness_error(tmp_path, monkeypatch):
    task = tmp_path / "task.json"
    task.write_text(json.dumps({"instance_id": "i", "repo": "r", "base_commit": "b",
                                "problem_statement": "p"}))
    monkeypatch.setattr(runner, "run_cell",
                        lambda *a, **k: {"status": "error", "error": "declared"})
    monkeypatch.setattr(sys, "argv", ["runner.py", "--arm", "worker",
                                      "--task-json", str(task), "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runner.main()
    assert exc.value.code != 0


def test_watchdog_kills_tool_process_at_shared_deadline(tmp_path):
    marker = tmp_path / "late.txt"
    deadline_file = tmp_path / "deadline.json"
    now = time.monotonic()
    deadline_file.write_text(json.dumps({
        "started_monotonic": now,
        "deadline_monotonic": now + 0.5,
        "seconds": 1,
    }), encoding="utf-8")
    command = [sys.executable, "-c",
               f"import time; time.sleep(3); open({str(marker)!r}, 'w').write('late')"]
    result = runner.run_with_watchdog(
        command, deadline_file, cleanup=lambda: None,
        setup_grace=5, poll=0.05,
    )
    assert result["timed_out"] is True
    time.sleep(0.5)
    assert not marker.exists()
    assert result["deadline_monotonic"] is not None


def test_control_collision_aborts_before_overwriting_repo_docs(tmp_path):
    source, base = _repo(tmp_path)
    (source / "docs").mkdir()
    (source / "docs" / "manager.md").write_text("project docs\n", encoding="utf-8")
    runner._git(["add", "."], cwd=source)
    runner._git(["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "docs"], cwd=source)
    base = runner._git(["rev-parse", "HEAD"], cwd=source).stdout.strip()
    checkout = runner.clone_checkout(source, base, tmp_path / "collision" / "repo")
    row = {"instance_id": "repo__project-1", "repo": "repo/project", "base_commit": base,
           "problem_statement": "Fix core.py"}
    config = runner.bid_config(checkout, "http://127.0.0.1:1/v1/chat/completions", "mock")
    with pytest.raises(runner.RunnerError):
        runner.run_worker(checkout, row, config, MockBackend([]))
    assert (checkout / "docs" / "manager.md").read_text(encoding="utf-8") == "project docs\n"


def test_worker_only_runs_without_manager_and_excludes_controls(tmp_path):
    source, base = _repo(tmp_path)
    checkout = runner.clone_checkout(source, base, tmp_path / "checkout")
    row = {"instance_id": "repo__project-1", "repo": "repo/project", "base_commit": base,
           "problem_statement": "Create hello.py"}
    backend = MockBackend([
        {"role": "assistant", "content": "WRITE hello.py\nprint('hi')\nEND WRITE\nDone", "finish_reason": "stop"}
    ])
    config = runner.bid_config(checkout, "http://127.0.0.1:1/v1/chat/completions", "mock",
                               task_statement=row["problem_statement"])
    patch = runner.run_worker(checkout, row, config, backend,
                              extract_patch_fn=lambda: runner.extract_patch(checkout, base))
    assert "hello.py" in patch
    assert ".bid/" not in patch and "docs/todo.md" not in patch
    assert any("Create hello.py" in message.get("content", "")
               for message in backend.call_history[0]["messages"])


def test_budget_backend_counts_and_enforces_request_cap():
    class Fake:
        def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
            return {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    budget_backend = runner.BudgetBackend(Fake())
    budget_backend.run([], [], 4096)
    assert budget_backend.budget.used_tokens == 12
    with pytest.raises(ValueError):
        budget_backend.run([], [], 4097)


def test_budget_backend_propagates_role_to_backend():
    class Fake:
        role = None
        def run(self, *args, **kwargs):
            return {"content": "ok", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    fake = Fake()
    wrapped = runner.BudgetBackend(fake)
    wrapped.role = "worker"
    assert fake.role == "worker"
    assert wrapped.role == "worker"


def test_per_run_executor_uses_project_only_offline_container(monkeypatch, tmp_path):
    calls = []
    class Result:
        returncode = 0
        stdout = ""
        stderr = ""
    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Result()
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    project = tmp_path / "workspace" / "repo"
    project.mkdir(parents=True)
    execute = runner.per_run_executor("img", project, "cell-1")
    execute([runner.sys.executable, "-m", "pytest"], cwd=str(project), timeout=5)
    run_cmd = calls[0][0]
    assert run_cmd[:3] == ["docker", "run", "--rm"]
    assert "--network" in run_cmd and "none" in run_cmd
    assert "--cap-drop" in run_cmd and "ALL" in run_cmd
    assert f"{project.resolve()}:/testbed" in run_cmd
    assert ":/work" not in " ".join(run_cmd)
    assert "docker.sock" not in " ".join(run_cmd)
    assert run_cmd[-3:] == ["python", "-m", "pytest"]
    assert calls[0][1]["check"] is False


def test_mini_cell_writes_config_and_prediction(monkeypatch, tmp_path):
    workspace = tmp_path / "run" / "repo"
    workspace.mkdir(parents=True)
    row = {"instance_id": "repo__project-1", "repo": "repo/project",
           "base_commit": "deadbeef", "problem_statement": "Fix it"}
    monkeypatch.setattr(runner, "run_with_watchdog",
                        lambda *a, **k: {"returncode": 0, "timed_out": False, "output": "",
                                         "setup_seconds": 1.0, "anchor_monotonic": 2.0,
                                         "deadline_monotonic": 602.0})
    monkeypatch.setattr(runner, "extract_patch",
                        lambda w, b: "diff --git a/core.py b/core.py\n--- a/core.py\n+++ b/core.py\n@@ -1 +1 @@\n-a\n+b\n")
    result = runner.run_mini_cell(workspace, row, "img", "m", "http://proxy/v1",
                                  "python", tmp_path / "cell", seed=7, timeout=5)
    assert result["status"] == "ok"
    assert (tmp_path / "cell" / "mini_pilot.yaml").read_text(encoding="utf-8").count("docker") >= 1
    assert "seed: 7" in (tmp_path / "cell" / "mini_pilot.yaml").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "cell" / "prediction.jsonl").read_text(encoding="utf-8"))
    assert saved["model_patch"].startswith("diff --git a/core.py")


def test_error_after_genuine_change_preserves_real_patch(tmp_path, monkeypatch):
    source, base = _repo(tmp_path)
    checkout = runner.clone_checkout(source, base, tmp_path / "errrun" / "repo")
    row = {"instance_id": "repo__project-1", "repo": "repo/project", "base_commit": base,
           "problem_statement": "Fix core.py"}

    def explode(workspace, row, config, backend, **kwargs):
        (workspace / "core.py").write_text("value = 2\n", encoding="utf-8")
        raise RuntimeError("worker crashed after writing")

    monkeypatch.setattr(runner, "run_worker", explode)
    output = tmp_path / "cell" / "prediction.jsonl"
    result = runner.run_cell("worker", checkout, row, "http://127.0.0.1:1/v1/chat/completions",
                             "mock", 7, output, defer_extract=False)
    assert result["status"] == "error"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert "core.py" in saved["model_patch"] and "value = 2" in saved["model_patch"]
    status = json.loads(output.with_suffix(".status.json").read_text(encoding="utf-8"))
    assert status["terminal_status"] == "error"
    assert status["patch_empty"] is False
    assert status["patch_sha256"]


def test_empty_patch_is_marked_and_sentinel_is_test_neutral(tmp_path, monkeypatch):
    source, base = _repo(tmp_path)
    checkout = runner.clone_checkout(source, base, tmp_path / "emptyrun" / "repo")
    row = {"instance_id": "repo__project-1", "repo": "repo/project", "base_commit": base,
           "problem_statement": "Fix core.py"}
    monkeypatch.setattr(runner, "run_worker", lambda *a, **k: "")
    output = tmp_path / "cell" / "prediction.jsonl"
    result = runner.run_cell("worker", checkout, row, "http://127.0.0.1:1/v1/chat/completions",
                             "mock", 7, output, defer_extract=False)
    assert result["status"] == "ok"
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert ".bid-empty-patch" in saved["model_patch"]
    status = json.loads(output.with_suffix(".status.json").read_text(encoding="utf-8"))
    assert status["patch_empty"] is True


def test_task_loader_rejects_hidden_fields(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"instance_id": "i", "repo": "r", "base_commit": "b",
                                "problem_statement": "p", "test_patch": "secret"}))
    with pytest.raises(runner.RunnerError):
        runner.load_task(path)


def test_mini_command_uses_pinned_stock_config_and_yolo_exit():
    cmd = runner.mini_command("/repo", "issue", "Qwen3-4B-Instruct-2507-Q8_0", "traj.json")
    assert cmd[cmd.index("-c") + 1] == "mini.yaml"
    assert "-y" in cmd and "--exit-immediately" in cmd
    model_at = cmd.index("minisweagent.run.mini") + 1
    assert cmd[model_at:model_at + 2] == ["-m", "openai/Qwen3-4B-Instruct-2507-Q8_0"]
    assert "docker" in cmd


def test_mini_config_mounts_only_the_checkout_and_uses_isolated_network():
    checkout = Path("/tmp/run/repo").resolve()
    text = runner.mini_config_text("img", checkout, 7, "none", "cell-1")
    assert "environment_class: docker" in text
    assert json.dumps(f"{checkout}:/testbed") in text
    assert "docker.sock" not in text
    assert "--network" in text and '"none"' in text
    assert "bid-pilot-cell=cell-1" in text
    assert "mode: yolo" in text
    assert "seed: 7" in text and "max_tokens: 4096" in text
    assert "top_k: 20" in text and "temperature: 0.6" in text


def test_bid_config_enables_repo_navigation(tmp_path):
    # Without MAP/FIND the Worker can only guess paths in an unseen repository (v1 pilot, 20/20 BID errors).
    config = runner.bid_config(tmp_path, "http://127.0.0.1:1/v1/chat/completions", "mock")
    assert config["repo_context_mode"] == "tools"
