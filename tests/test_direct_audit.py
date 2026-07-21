import json
import os
import tempfile
import time

import pytest

from bid import adapter, harness, model, permissions, search, vc


def text_response(text):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": None,
        "finish_reason": "stop",
    }


def worker_config(workspace, **overrides):
    config = {
        "workspace": workspace,
        "max_tokens": 256,
        "worker_timeout": 2,
        "inactivity_timeout": 1,
        "repeat_action_limit": 2,
        "max_searches_per_worker": 1,
    }
    config.update(overrides)
    return config


def prepare_worker(workspace):
    os.makedirs(os.path.join(workspace, "docs"), exist_ok=True)
    with open(os.path.join(workspace, "docs", "todo.md"), "w", encoding="utf-8") as handle:
        handle.write("- [ ] T1 — Test\n")
    with open(os.path.join(workspace, "docs", "worker.md"), "w", encoding="utf-8") as handle:
        handle.write("# Worker\n")
    vc.VersionControl(workspace).init()


class CountingProvider(search.SearchProvider):
    provider_name = "counting"

    def __init__(self, result_count=1):
        self.calls = 0
        self.result_count = result_count

    def search(self, query, max_results=5):
        self.calls += 1
        return [
            search.SearchResult(
                f"Result {index}",
                f"https://example.com/{index}",
                f"Summary {index}",
            )
            for index in range(self.result_count)
        ], None


def test_cache_hit_materializes_task_local_evidence():
    with tempfile.TemporaryDirectory() as workspace:
        cache = search.SearchCache(workspace)
        provider = CountingProvider()
        first, _, error, cached = search.execute_search(
            workspace, 1, "Same Query", cache, provider
        )
        assert error is None and not cached
        second, count, error, cached = search.execute_search(
            workspace, 2, " same   query ", search.SearchCache(workspace), provider
        )
        assert error is None and cached
        assert count == 1
        assert first.startswith("docs/research/T1/")
        assert second.startswith("docs/research/T2/")
        assert first != second
        assert os.path.isfile(os.path.join(workspace, second))
        assert provider.calls == 1


def test_stale_cache_entry_is_researched():
    with tempfile.TemporaryDirectory() as workspace:
        cache = search.SearchCache(workspace)
        provider = CountingProvider()
        path, _, _, _ = search.execute_search(workspace, 1, "topic", cache, provider)
        os.remove(os.path.join(workspace, path))
        new_path, _, error, cached = search.execute_search(
            workspace, 1, "topic", search.SearchCache(workspace), provider
        )
        assert error is None and not cached
        assert os.path.isfile(os.path.join(workspace, new_path))
        assert provider.calls == 2


def test_citations_must_belong_to_current_task():
    with tempfile.TemporaryDirectory() as workspace:
        provider = CountingProvider()
        cache = search.SearchCache(workspace)
        path, _, _, _ = search.execute_search(workspace, 2, "topic", cache, provider)
        research_dir = search._research_dir(workspace, 2)
        assert not search.has_citations(
            "See docs/research/T1/search-001.md", research_dir
        )
        assert search.has_citations(f"See {path}", research_dir)
        assert search.has_citations("Source: https://example.com/0", research_dir)


def test_empty_search_does_not_call_provider():
    with tempfile.TemporaryDirectory() as workspace:
        provider = CountingProvider()
        result = search.execute_search(
            workspace, 1, "   ", search.SearchCache(workspace), provider
        )
        assert result[2] == "empty search query"
        assert provider.calls == 0


def test_provider_output_is_bounded_after_return():
    with tempfile.TemporaryDirectory() as workspace:
        provider = CountingProvider(result_count=20)
        _, count, error, _ = search.execute_search(
            workspace, 1, "topic", search.SearchCache(workspace), provider
        )
        assert error is None
        assert count == 5


def test_invalid_read_is_a_stall_not_runtime_exception():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        backend = model.MockBackend([
            text_response("READ ../../etc/passwd"),
            text_response("READ ../../etc/passwd"),
            text_response("READ ../../etc/passwd"),
            text_response("READ ../../etc/passwd"),
        ])
        result = adapter.WorkerAdapter(worker_config(workspace), 1).run(backend)
        assert result["status"] in ("stalled", "timeout")
        assert "UnboundLocalError" not in result.get("reason", "")


def test_repeated_identical_write_is_not_progress():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        backend = model.MockBackend([
            text_response("WRITE result.md\nsame\nEND WRITE"),
            text_response("WRITE result.md\nsame\nEND WRITE"),
            text_response("WRITE result.md\nsame\nEND WRITE"),
            text_response("WRITE result.md\nsame\nEND WRITE"),
            text_response("WRITE result.md\nsame\nEND WRITE"),
            text_response("WRITE result.md\nsame\nEND WRITE"),
        ])
        result = adapter.WorkerAdapter(worker_config(workspace), 1).run(backend)
        assert result["status"] in ("stalled", "timeout")
        with open(os.path.join(workspace, "result.md"), encoding="utf-8") as handle:
            assert handle.read() == "same"


def test_cache_hits_do_not_consume_network_budget():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        seed = CountingProvider()
        search.execute_search(
            workspace, 1, "cached", search.SearchCache(workspace), seed
        )
        provider = CountingProvider()
        backend = model.MockBackend([
            text_response("SEARCH cached"),
            text_response("SEARCH live"),
            text_response("SEARCH blocked"),
            text_response("SEARCH blocked"),
        ])
        adapter.WorkerAdapter(
            worker_config(workspace, max_searches_per_worker=1),
            1,
            search_provider=provider,
        ).run(backend)
        assert provider.calls == 1


def test_worker_cannot_read_harness_control_state():
    assert not permissions.check_read_permission(
        ".bid/current", permissions.ROLE_WORKER
    )[0]
    assert not permissions.check_read_permission(
        "docs/reviews/T1.md", permissions.ROLE_WORKER
    )[0]
    assert permissions.check_read_permission(
        "docs/research/T1/search-001.md", permissions.ROLE_WORKER
    )[0]


def test_vc_recovers_dead_lock():
    with tempfile.TemporaryDirectory() as workspace:
        system = vc.VersionControl(workspace)
        system.init()
        with open(system.lock_file, "w", encoding="utf-8") as handle:
            json.dump({"pid": 999999999, "token": "dead"}, handle)
        assert system.save_state("test", "lock recovery") == "s1"


def test_vc_commit_failure_restores_current(monkeypatch):
    with tempfile.TemporaryDirectory() as workspace:
        system = vc.VersionControl(workspace)
        system.init()

        def fail(*args, **kwargs):
            raise OSError("log failure")

        monkeypatch.setattr(system, "_append_log", fail)
        with pytest.raises(OSError):
            system.save_state("test", "failure")
        assert system.get_current() == "s0"
        assert system._list_states() == ["s0"]


def test_vc_restore_failure_restores_workspace_and_metadata(monkeypatch):
    with tempfile.TemporaryDirectory() as workspace:
        path = os.path.join(workspace, "value.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("old")
        system = vc.VersionControl(workspace)
        system.init()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("new")
        system.save_state("test", "new value")

        def fail(*args, **kwargs):
            raise OSError("truncate failure")

        monkeypatch.setattr(system, "_truncate_log", fail)
        with pytest.raises(OSError):
            system.restore("s0")
        with open(path, encoding="utf-8") as handle:
            assert handle.read() == "new"
        assert system.get_current() == "s1"


def test_old_live_run_lock_is_not_stolen():
    with tempfile.TemporaryDirectory() as workspace:
        os.makedirs(os.path.join(workspace, ".bid"), exist_ok=True)
        path = os.path.join(workspace, ".bid", "run.lock")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "token": "live"}, handle)
        old = time.time() - 3600
        os.utime(path, (old, old))
        assert not harness._run_lock_is_stale(path)
        assert harness._existing_run_is_live(workspace)


def test_init_refuses_to_replace_running_workspace():
    with tempfile.TemporaryDirectory() as parent:
        workspace = os.path.join(parent, "workspace")
        os.makedirs(os.path.join(workspace, ".bid"), exist_ok=True)
        marker = os.path.join(workspace, "keep.txt")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("keep")
        with open(os.path.join(workspace, ".bid", "run.lock"), "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "token": "live"}, handle)
        result = harness.init_project(
            "replace me",
            worker_config(workspace),
            backend=model.MockBackend([text_response("- [ ] T1 — New")]),
        )
        assert result["status"] == "error"
        with open(marker, encoding="utf-8") as handle:
            assert handle.read() == "keep"
