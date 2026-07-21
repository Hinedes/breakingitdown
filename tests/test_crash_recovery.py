import json
import os
import tempfile
import time

import pytest

from bid import adapter, harness, model, search, vc


def text_response(text):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
    }


def prepare_project(workspace, checked=False):
    os.makedirs(os.path.join(workspace, "docs"), exist_ok=True)
    marker = "x" if checked else " "
    with open(os.path.join(workspace, "docs", "todo.md"), "w", encoding="utf-8") as handle:
        handle.write(f"- [{marker}] T1 — Test\n")
    with open(os.path.join(workspace, "docs", "worker.md"), "w", encoding="utf-8") as handle:
        handle.write("# Worker\n")
    return vc.VersionControl(workspace)


def config(workspace, **overrides):
    value = {
        "workspace": workspace,
        "max_tokens": 256,
        "worker_timeout": 2,
        "inactivity_timeout": 1,
        "repeat_action_limit": 2,
        "max_searches_per_worker": 1,
    }
    value.update(overrides)
    return value


def test_recover_unchecked_worker_rolls_back_dirty_tree():
    with tempfile.TemporaryDirectory() as workspace:
        system = prepare_project(workspace)
        system.init()
        harness._begin_active_session(workspace, "worker", "s0", 1)
        with open(os.path.join(workspace, "dirty.txt"), "w", encoding="utf-8") as handle:
            handle.write("unfinished")

        result = harness._recover_active_session(workspace)

        assert result["status"] == "rolled-back"
        assert not os.path.exists(os.path.join(workspace, "dirty.txt"))
        assert system.get_current() == "s0"
        assert not os.path.exists(harness._active_session_path(workspace))


def test_recover_checked_worker_commits_abnormal_submission():
    with tempfile.TemporaryDirectory() as workspace:
        system = prepare_project(workspace)
        system.init()
        harness._begin_active_session(workspace, "worker", "s0", 1)
        with open(os.path.join(workspace, "docs", "todo.md"), "w", encoding="utf-8") as handle:
            handle.write("- [x] T1 — Test\n")
        with open(os.path.join(workspace, "artifact.md"), "w", encoding="utf-8") as handle:
            handle.write("finished before crash")

        result = harness._recover_active_session(workspace)

        assert result["status"] == "submitted"
        assert system.get_current() == "s1"
        assert os.path.exists(os.path.join(workspace, "artifact.md"))
        assert not os.path.exists(harness._active_session_path(workspace))


def test_marker_left_after_commit_does_not_duplicate_state():
    with tempfile.TemporaryDirectory() as workspace:
        system = prepare_project(workspace, checked=True)
        system.init()
        harness._begin_active_session(workspace, "worker", "s0", 1)
        system.save_state("Worker 1", "already committed")

        result = harness._recover_active_session(workspace)

        assert result["status"] == "already-committed"
        assert system._list_states() == ["s0", "s1"]
        assert not os.path.exists(harness._active_session_path(workspace))


def test_interrupted_review_is_rolled_back():
    with tempfile.TemporaryDirectory() as workspace:
        system = prepare_project(workspace)
        system.init()
        harness._begin_active_session(workspace, "review", "s0")
        with open(os.path.join(workspace, "docs", "review-dirty.md"), "w", encoding="utf-8") as handle:
            handle.write("partial review")

        result = harness._recover_active_session(workspace)

        assert result["status"] == "rolled-back"
        assert not os.path.exists(os.path.join(workspace, "docs", "review-dirty.md"))


def test_backed_up_init_journal_restores_previous_workspace():
    with tempfile.TemporaryDirectory() as parent:
        workspace = os.path.join(parent, "workspace")
        backup = os.path.join(parent, "backup")
        os.makedirs(workspace)
        with open(os.path.join(workspace, "old.txt"), "w", encoding="utf-8") as handle:
            handle.write("old")
        os.rename(workspace, backup)
        os.makedirs(workspace)
        with open(os.path.join(workspace, "partial.txt"), "w", encoding="utf-8") as handle:
            handle.write("partial")
        harness._write_json_atomic(harness._init_journal_path(workspace), {
            "phase": "backed-up",
            "workspace": workspace,
            "workspace_existed": True,
            "backup": backup,
        })

        result = harness._recover_init_journal(workspace)

        assert result["status"] == "restored-old"
        assert os.path.isfile(os.path.join(workspace, "old.txt"))
        assert not os.path.exists(os.path.join(workspace, "partial.txt"))


def test_run_takes_lock_before_touching_workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as parent:
        workspace = os.path.join(parent, "workspace")
        original = harness.ensure_workspace

        def checked(target):
            assert os.path.exists(harness._workspace_lock_path(target))
            return original(target)

        monkeypatch.setattr(harness, "ensure_workspace", checked)
        result = harness.run_project(config(workspace), backend=model.MockBackend([]))
        assert result["status"] == "error"
        assert "no BID project" in result["reason"]


def test_varied_failures_still_trigger_no_progress_recovery():
    with tempfile.TemporaryDirectory() as workspace:
        system = prepare_project(workspace)
        system.init()
        backend = model.MockBackend([
            text_response("READ missing-a"),
            text_response("READ missing-b"),
            text_response("READ missing-c"),
            text_response("READ missing-d"),
            text_response("READ missing-e"),
            text_response("READ missing-f"),
            text_response("READ missing-g"),
            text_response("READ missing-h"),
        ])
        result = adapter.WorkerAdapter(config(workspace), 1).run(backend)
        assert result["status"] in ("stalled", "timeout")


def test_search_cache_lock_is_not_stolen_from_live_owner():
    with tempfile.TemporaryDirectory() as workspace:
        cache = search.SearchCache(workspace)
        with open(cache._lock_path, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "token": "live"}, handle)
        old = time.time() - 3600
        os.utime(cache._lock_path, (old, old))
        assert not cache._lock_is_stale()


def test_vc_partial_backup_failure_restores_all_live_files(monkeypatch):
    with tempfile.TemporaryDirectory() as workspace:
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(workspace, name), "w", encoding="utf-8") as handle:
                handle.write("old")
        system = vc.VersionControl(workspace)
        system.init()
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(workspace, name), "w", encoding="utf-8") as handle:
                handle.write("new")
        system.save_state("test", "new")

        original_move = __import__("shutil").move
        calls = {"live": 0}

        def flaky_move(source, destination, *args, **kwargs):
            if os.path.dirname(source) == workspace and os.path.basename(source) != ".bid":
                calls["live"] += 1
                if calls["live"] == 2:
                    raise OSError("simulated partial backup failure")
            return original_move(source, destination, *args, **kwargs)

        monkeypatch.setattr("bid.vc.shutil.move", flaky_move)
        with pytest.raises(OSError):
            system.restore("s0")
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(workspace, name), encoding="utf-8") as handle:
                assert handle.read() == "new"
        assert system.get_current() == "s1"
