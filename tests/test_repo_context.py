import hashlib
import json
import os
import tempfile
import time

import pytest

from bid import adapter, harness, model, permissions, repo_context, vc


def write_file(root, relative, content, mode=None):
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as file:
        file.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    if mode is not None:
        os.chmod(path, mode)
    return path


def worker_config(workspace, mode="off", **overrides):
    config = {
        "workspace": workspace,
        "max_tokens": 256,
        "worker_timeout": 10,
        "inactivity_timeout": 10,
        "repeat_action_limit": 3,
        "repo_context_mode": mode,
        "repo_context_max_chars": 12000,
        "repo_context_max_find_hits": 100,
        "repo_context_max_finds": 32,
        "repo_context_find_closure_max_chars": 8000,
        "repo_context_max_file_bytes": 1048576,
    }
    config.update(overrides)
    return config


def prepare_worker(workspace):
    write_file(workspace, "docs/todo.md", "- [ ] T1 — Inspect files\n")
    write_file(workspace, "docs/worker.md", "# Worker\n")
    vc.VersionControl(workspace).init()


def text_response(content):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": None,
        "finish_reason": "stop",
    }


def check_task_command():
    return "WRITE docs/todo.md\n- [x] T1 — Inspect files\nEND WRITE\nDone"


def run_worker(workspace, mode, responses, **config_overrides):
    config = worker_config(workspace, mode, **config_overrides)
    backend = model.MockBackend([text_response(response) for response in responses])
    result = adapter.WorkerAdapter(config, 1).run(backend)
    return result, backend


def closure_packet(text):
    marker = "# FIND evidence closure"
    assert marker in text
    return text[text.index(marker):]


def test_index_refresh_and_freshness():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "b.txt", "beta")
        write_file(workspace, "a.txt", "alpha")
        first = repo_context.refresh_index(workspace)
        assert first["added"] == 2
        index = repo_context.load_index(workspace)
        assert index["schema_version"] == repo_context.INDEX_SCHEMA_VERSION
        assert index["extractor_version"] == repo_context.EXTRACTOR_VERSION
        assert list(index["entries"]) == ["a.txt", "b.txt"]
        assert index["entries"]["a.txt"]["classification"] == "text"
        assert index["entries"]["a.txt"]["sha256"]

        second = repo_context.refresh_index(workspace)
        assert second["reused"] == 2
        assert second["added"] == second["changed"] == second["removed"] == 0

        write_file(workspace, "c.txt", "gamma")
        write_file(workspace, "a.txt", "alpha changed")
        os.remove(os.path.join(workspace, "b.txt"))
        third = repo_context.refresh_index(workspace)
        assert third["added"] == 1
        assert third["changed"] == 1
        assert third["removed"] == 1
        assert "b.txt" not in repo_context.load_index(workspace)["entries"]


@pytest.mark.parametrize("bad_index", ["{", {"schema_version": 999}, {"schema_version": 1}])
def test_corrupt_or_unknown_index_is_rebuilt(bad_index):
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "file.txt", "content")
        index_path = os.path.join(workspace, ".bid", "repo_context", "index.json")
        os.makedirs(os.path.dirname(index_path), exist_ok=True)
        with open(index_path, "w", encoding="utf-8") as file:
            if isinstance(bad_index, str):
                file.write(bad_index)
            else:
                json.dump(bad_index, file)
        stats = repo_context.refresh_index(workspace)
        assert stats["added"] == 1
        assert repo_context.load_index(workspace)["entries"]["file.txt"]


def test_atomic_failure_keeps_previous_index():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "file.txt", "old")
        repo_context.refresh_index(workspace)
        index_path = os.path.join(workspace, ".bid", "repo_context", "index.json")
        with open(index_path, "rb") as file:
            previous = file.read()
        write_file(workspace, "file.txt", "new")
        original_replace = repo_context.os.replace

        def fail_replace(source, destination):
            raise OSError("simulated replace failure")

        repo_context.os.replace = fail_replace
        try:
            with pytest.raises(repo_context.RepositoryContextError):
                repo_context.refresh_index(workspace)
        finally:
            repo_context.os.replace = original_replace
        with open(index_path, "rb") as file:
            assert file.read() == previous
        assert repo_context.load_index(workspace)["entries"]["file.txt"]["text"] == "old"


@pytest.mark.parametrize("nested", [False, True])
def test_index_never_follows_control_directory_symlinks(nested):
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
        write_file(workspace, "project.txt", "project")
        if nested:
            os.makedirs(os.path.join(workspace, ".bid"))
            os.symlink(outside, os.path.join(workspace, ".bid", "repo_context"))
        else:
            os.symlink(outside, os.path.join(workspace, ".bid"))

        with pytest.raises(repo_context.RepositoryContextError):
            repo_context.refresh_index(workspace)
        assert not os.path.exists(os.path.join(outside, "repo_context", "index.json"))


def test_index_rejects_tampered_cached_text():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "file.txt", "actual")
        repo_context.refresh_index(workspace)
        index_path = os.path.join(workspace, ".bid", "repo_context", "index.json")
        with open(index_path, encoding="utf-8") as file:
            index = json.load(file)
        index["entries"]["file.txt"]["text"] = "tampered"
        with open(index_path, "w", encoding="utf-8") as file:
            json.dump(index, file)

        assert repo_context.load_index(workspace) is None
        context = repo_context.RepositoryContext(workspace)
        context.refresh()
        assert context.index["entries"]["file.txt"]["text"] == "actual"

        with open(index_path, encoding="utf-8") as file:
            index = json.load(file)
        index["entries"]["file.txt"]["text"] = "\ud800"
        with open(index_path, "w", encoding="utf-8") as file:
            json.dump(index, file)
        assert repo_context.load_index(workspace) is None


def test_classification_exclusions_and_symlink_safety():
    with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
        write_file(workspace, "text.txt", "text")
        write_file(workspace, "binary.bin", b"\x00\x01")
        write_file(workspace, "invalid.bin", b"\xff\xfe")
        write_file(workspace, "large.dat", b"123456789")
        for directory in (".git", ".hg", ".svn", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"):
            write_file(workspace, f"{directory}/hidden.txt", "ignored")
        write_file(workspace, "docs/todo.md", "control")
        write_file(workspace, "docs/work.md", "project")
        write_file(workspace, ".bid/private.txt", "control")
        write_file(outside, "outside.txt", "outside")
        os.symlink(os.path.join(workspace, "text.txt"), os.path.join(workspace, "inside-link"))
        os.symlink(os.path.join(outside, "outside.txt"), os.path.join(workspace, "outside-link"))
        os.symlink(os.path.join(workspace, "nested"), os.path.join(workspace, "dir-link"))

        context = repo_context.RepositoryContext(workspace, max_file_bytes=8)
        context.refresh()
        entries = context.index["entries"]
        assert entries["binary.bin"]["classification"] == "binary"
        assert entries["invalid.bin"]["classification"] == "binary"
        assert entries["large.dat"]["classification"] == "oversized"
        assert entries["inside-link"]["classification"] == "symlink"
        assert entries["outside-link"]["classification"] == "symlink"
        assert entries["dir-link"]["classification"] == "symlink"
        assert not any(path.startswith(".git/") for path in entries)
        assert "docs/todo.md" not in entries
        assert ".bid/private.txt" not in entries
        assert "outside.txt" not in entries
        assert "outside-link" in entries


def test_unusual_filenames_remain_indexable_and_display_safe():
    with tempfile.TemporaryDirectory() as workspace:
        backslash_name = "slash\\name.txt"
        control_name = "line\nname.txt"
        write_file(workspace, backslash_name, "needle")
        write_file(workspace, control_name, "needle")

        context = repo_context.RepositoryContext(workspace)
        context.refresh()

        assert repo_context.load_index(workspace) is not None
        assert context.find_result("needle", refresh=False)["total_matches"] == 2
        map_output = context.map(refresh=False)
        find_output = context.find("needle", refresh=False)
        assert "slash\\\\name.txt" in map_output
        assert "line\\nname.txt" in map_output
        assert "line\nname.txt" not in map_output
        assert "line\\nname.txt:1:" in find_output


def test_map_is_bounded_scoped_and_deterministic():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "z.txt", "z")
        write_file(workspace, "a.txt", "a")
        write_file(workspace, "service/handler.go", "handler")
        write_file(workspace, "service/readme.md", "service")
        context = repo_context.RepositoryContext(workspace, max_chars=35)
        context.refresh()
        root_map = context.map(refresh=False)
        assert len(root_map) <= 35
        assert "omitted" in root_map
        assert "service/" in root_map or "a.txt" in root_map
        assert "service/handler.go" not in context.map("other", refresh=False)
        context.max_chars = 120
        scoped = context.map("service", refresh=False)
        assert "service/handler.go" in scoped
        assert "a.txt" not in scoped
        assert not os.path.isabs(scoped.splitlines()[0])

        first_bytes = open(context.index_path, "rb").read()
        context.refresh()
        assert open(context.index_path, "rb").read() == first_bytes


def test_literal_search_counts_and_caps_hits():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "b.txt", "x x\nnone\n")
        write_file(workspace, "a.txt", "x\nx .* x\n")
        write_file(workspace, "binary.bin", b"\x00x")
        context = repo_context.RepositoryContext(workspace, max_find_hits=2)
        context.refresh()
        result = context.find_result("x", refresh=False)
        assert result["total_matches"] == 5
        assert result["truncated"]
        assert len(result["hits"]) == 2
        assert result["hits"][0]["path"] == "a.txt"
        assert result["hits"][0]["line"] == 1
        assert "omitted matches: 3" in result["output"]
        assert "binary=1" in result["output"]
        assert context.find_result(".*", refresh=False)["total_matches"] == 1
        empty = context.find_result("", refresh=False)
        assert not empty["ok"]
        assert "must not be empty" in empty["output"]
        assert "matches: 0" in context.find_result("missing", refresh=False)["output"]


def test_search_refreshes_after_modification():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "file.txt", "old")
        context = repo_context.RepositoryContext(workspace)
        assert context.find_result("new")["total_matches"] == 0
        write_file(workspace, "file.txt", "new")
        now = time.time_ns() + 2_000_000_000
        os.utime(os.path.join(workspace, "file.txt"), ns=(now, now))
        assert context.find_result("new")["total_matches"] == 1


def test_map_and_find_path_safety_in_worker():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "service/handler.go", "handler")
        responses = ["MAP service", "FIND handler", check_task_command()]
        result, backend = run_worker(workspace, "tools", responses)
        assert result["status"] == "done"
        user_results = [
            message["content"]
            for call in backend.call_history
            for message in call["messages"]
            if message["role"] == "user" and message["content"].startswith(("service/", "FIND is"))
        ]
        assert any("service/handler.go" in content for content in user_results)
        assert any("service/handler.go:1" in content for content in user_results)


def test_parser_only_exposes_context_commands_in_tools_modes():
    off = adapter._parse_content_into_turns("MAP\nFIND .*", "off")
    tools = adapter._parse_content_into_turns("MAP service\nFIND .*", "tools")
    assert adapter._find_unknown_commands("MAP\nFIND .*", off, "off") == ["MAP", "FIND .*"]
    assert tools == [
        {"type": "MAP", "path": "service"},
        {"type": "FIND", "query": ".*"},
    ]


def test_protected_paths_are_not_mappable_or_searchable():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, ".bid/secret.txt", "control")
        write_file(workspace, "docs/reviews/review.md", "control")
        write_file(workspace, "docs/research/research.md", "control")
        write_file(workspace, "project.txt", "project")
        responses = ["MAP ../outside", "MAP .bid", "FIND control", check_task_command()]
        result, backend = run_worker(workspace, "tools", responses)
        assert result["status"] == "done"
        all_user_content = [
            message["content"]
            for call in backend.call_history
            for message in call["messages"]
            if message["role"] == "user"
        ]
        assert any("path traversal denied" in content for content in all_user_content)
        assert any("excluded" in content for content in all_user_content)
        find_output = next(content for content in all_user_content if content.startswith("FIND is"))
        assert "matches: 0" in find_output
        assert "/" not in find_output.splitlines()[-1].split("skipped:", 1)[0]


def test_indexing_failure_warns_and_worker_continues(monkeypatch):
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        original_refresh = repo_context.RepositoryContext.refresh

        def fail_refresh(self):
            raise OSError("index unavailable")

        monkeypatch.setattr(repo_context.RepositoryContext, "refresh", fail_refresh)
        result, backend = run_worker(workspace, "inject", [check_task_command()])
        assert result["status"] == "done"
        assert "Repository context warning" in backend.call_history[0]["messages"][1]["content"]
        monkeypatch.setattr(repo_context.RepositoryContext, "refresh", original_refresh)


def test_unreadable_file_is_marked_and_skipped(monkeypatch):
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "secret.txt", "secret")
        original_open = repo_context.open if hasattr(repo_context, "open") else open

        def deny_secret(path, *args, **kwargs):
            if str(path).endswith("secret.txt"):
                raise PermissionError("denied")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(repo_context, "open", deny_secret, raising=False)
        context = repo_context.RepositoryContext(workspace)
        context.refresh()
        assert context.index["entries"]["secret.txt"]["classification"] == "unreadable"
        assert "secret.txt [unreadable]" in context.map(refresh=False)
        result = context.find_result("secret", refresh=False)
        assert result["total_matches"] == 0
        assert result["skipped"]["unreadable"] == 1


def test_modes_and_compatibility_alias(monkeypatch):
    monkeypatch.delenv("BID_REPO_CONTEXT", raising=False)
    assert harness.get_config()["repo_context_mode"] == "off"
    monkeypatch.setenv("BID_REPO_CONTEXT", "tools")
    assert harness.get_config()["repo_context_mode"] == "tools"
    monkeypatch.setenv("BID_REPO_CONTEXT", "1")
    assert harness.get_config()["repo_context_mode"] == "inject"
    monkeypatch.setenv("BID_REPO_CONTEXT_MAX_FINDS", "7")
    assert harness.get_config()["repo_context_max_finds"] == 7
    monkeypatch.setenv("BID_REPO_CONTEXT_FIND_CLOSURE_MAX_CHARS", "321")
    assert harness.get_config()["repo_context_find_closure_max_chars"] == 321
    monkeypatch.setenv("BID_REPO_CONTEXT", "unexpected")
    with pytest.raises(ValueError, match="BID_REPO_CONTEXT"):
        harness.get_config()


@pytest.mark.parametrize("mode", ["off", "tools", "inject"])
def test_mode_prompt_matrix(mode):
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "project.txt", "project")
        result, backend = run_worker(workspace, mode, [check_task_command()])
        assert result["status"] == "done"
        prompt = backend.call_history[0]["messages"][1]["content"]
        if mode == "off":
            assert "MAP" not in prompt
            assert "FIND" not in prompt
            assert "Workspace orientation" not in prompt
            assert "Repository tools are available" not in prompt
        elif mode == "tools":
            assert "Repository tools are available" in prompt
            assert "Workspace orientation" not in prompt
        else:
            assert "# Workspace orientation" in prompt
            assert "project.txt" in prompt
            assert len(prompt.rsplit("# Workspace orientation", 1)[1]) <= 12000


def test_off_repeated_read_is_baseline_and_tools_deduplicate():
    for mode, expected_duplicate in (("off", False), ("tools", True)):
        with tempfile.TemporaryDirectory() as workspace:
            prepare_worker(workspace)
            write_file(workspace, "file.txt", "content")
            responses = ["READ file.txt", "READ file.txt", check_task_command()]
            result, backend = run_worker(workspace, mode, responses)
            assert result["status"] == "done"
            read_results = [
                message["content"]
                for call in backend.call_history
                for message in call["messages"]
                if message["role"] == "user" and message["content"] == "content"
            ]
            duplicate_results = [
                message["content"]
                for call in backend.call_history
                for message in call["messages"]
                if message["role"] == "user" and message["content"].startswith("unchanged:")
            ]
            assert bool(duplicate_results) is expected_duplicate
            if mode == "off":
                assert len(read_results) >= 2


def test_tools_and_inject_soft_reset_context():
    for mode, wants_orientation in (("tools", False), ("inject", True)):
        with tempfile.TemporaryDirectory() as workspace:
            prepare_worker(workspace)
            write_file(workspace, "file.txt", "content")
            responses = ["READ file.txt", "READ file.txt", "READ file.txt", check_task_command()]
            result, backend = run_worker(
                workspace, mode, responses, repeat_action_limit=1
            )
            assert result["status"] == "done"
            reset_prompts = [
                call["messages"][1]["content"]
                for call in backend.call_history[1:]
                if "did not make progress" in call["messages"][1]["content"]
            ]
            assert reset_prompts
            assert ("# Workspace orientation" in reset_prompts[0]) is wants_orientation
            assert "Repository tools are available" in reset_prompts[0]


def test_read_ledger_invalidates_on_write_and_is_per_adapter():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "old")
        responses = [
            "READ file.txt",
            "WRITE file.txt\nnew\nEND WRITE",
            "READ file.txt",
            check_task_command(),
        ]
        result, backend = run_worker(workspace, "tools", responses)
        assert result["status"] == "done"
        assert any(
            message["content"] == "new"
            for call in backend.call_history
            for message in call["messages"]
            if message["role"] == "user"
        )

        second_backend = model.MockBackend([text_response("READ file.txt"), text_response(check_task_command())])
        second = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1).run(second_backend)
        assert second["status"] == "done"
        assert not any(
            message["content"].startswith("unchanged:")
            for call in second_backend.call_history
            for message in call["messages"]
            if message["role"] == "user"
        )


def test_context_events_are_bounded_and_include_mode():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "value")
        result, backend = run_worker(
            workspace, "tools", ["MAP", "FIND value", "READ file.txt", "READ file.txt", check_task_command()]
        )
        assert result["status"] == "done"
        names = [event["event"] for event in result["observability_events"]]
        assert "repo_context_refresh" in names
        assert "repo_context_map" in names
        assert "repo_context_find" in names
        assert "repo_context_read_deduplicated" in names
        assert all(event["mode"] == "tools" for event in result["observability_events"])
        assert all(len(str(event)) < 600 for event in result["observability_events"])


def test_find_novelty_handles_zero_positive_and_changed_results():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", ("needle\n" * 120).encode())
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)

        first_zero = worker._find_command("missing")
        assert "matches: 0" in first_zero
        assert worker._last_find_useful is True

        duplicate_zero = worker._find_command("missing")
        assert duplicate_zero.startswith("unchanged:")
        assert "previous matches: 0" in duplicate_zero
        assert worker._last_find_useful is False

        write_file(workspace, "file.txt", ("needle\nmissing\n" * 120).encode())
        changed_result = worker._find_command("missing")
        assert "matches: 120" in changed_result
        assert worker._last_find_useful is True

        first_positive = worker._find_command("needle")
        duplicate_positive = worker._find_command("needle")
        assert "file.txt:1:" in first_positive
        assert duplicate_positive.startswith("unchanged:")
        assert len(duplicate_positive) < len(first_positive)
        assert worker._last_find_useful is False

        outcomes = [
            event["outcome"]
            for event in worker._repo_event_entries
            if event["event"] == "repo_context_find"
        ]
        assert outcomes == ["executed", "deduplicated", "executed", "executed", "deduplicated"]


def test_search_fingerprint_is_stable_and_ignores_index_files():
    with tempfile.TemporaryDirectory() as workspace:
        write_file(workspace, "file.txt", "needle")
        context = repo_context.RepositoryContext(workspace)
        fingerprints = []
        for _ in range(4):
            context.refresh()
            fingerprints.append(repo_context.search_state_fingerprint(context.index))
        now = time.time_ns() + 2_000_000_000
        os.utime(os.path.join(workspace, "file.txt"), ns=(now, now))
        context.refresh()
        fingerprints.append(repo_context.search_state_fingerprint(context.index))
        write_file(workspace, ".bid/repo_context/index.tmp", "temporary")
        context.refresh()
        fingerprints.append(repo_context.search_state_fingerprint(context.index))

        assert len(set(fingerprints)) == 1


def test_failed_find_refresh_never_authorizes_a_stale_query():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle")
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)
        assert "matches: 1" in worker._find_command("needle")

        original_refresh = worker._repo_context.refresh

        def fail_refresh():
            raise OSError("refresh failed")

        worker._repo_context.refresh = fail_refresh
        assert worker._find_command("needle").startswith("error:")
        assert worker._last_find_useful is False
        assert worker._repo_event_entries[-1]["outcome"] == "error"

        worker._repo_context.refresh = original_refresh
        assert worker._find_command("needle").startswith("unchanged:")
        assert worker._repo_event_entries[-1]["outcome"] == "deduplicated"


def test_find_ledger_preserves_exact_literal_semantics():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", 'Exception\n"Exception"\n exception \nclass.*Exception\n')
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)
        queries = ("Exception", '"Exception"', " exception ", "class.*Exception")

        for query in queries:
            assert "matches:" in worker._find_command(query)
        assert [
            event["outcome"]
            for event in worker._repo_event_entries
            if event["event"] == "repo_context_find"
        ] == ["executed"] * 4
        assert worker._find_command("Exception").startswith("unchanged:")
        assert worker._find_command('"Exception"').startswith("unchanged:")


def test_find_invalidates_across_searchable_file_state_changes():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "match.txt", "needle")
        write_file(workspace, "unrelated.txt", "other")
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_file_bytes=8), 1
        )

        queries = ["initial", "matching text", "unrelated text", "added", "deleted", "binary", "oversized", "shrunk"]
        outcomes = []
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "match.txt", "needle!")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "unrelated.txt", "changed")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "added.txt", "added")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        os.remove(os.path.join(workspace, "added.txt"))
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "match.txt", b"\x00abc")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "match.txt", b"123456789")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])
        write_file(workspace, "match.txt", "needle")
        worker._find_command("needle")
        outcomes.append(worker._repo_event_entries[-1]["outcome"])

        assert len(outcomes) == len(queries)
        assert outcomes == ["executed"] * len(queries)


def test_duplicate_find_participates_in_stall_detection():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        result, backend = run_worker(
            workspace,
            "tools",
            [
                'FIND "class.*Exception"',
                'FIND "class.*Exception"',
                'FIND "class.*Exception"',
                check_task_command(),
            ],
            repeat_action_limit=1,
        )
        assert result["status"] == "done"
        assert any(
            "did not make progress" in call["messages"][1]["content"]
            for call in backend.call_history
        )
        outcomes = [
            event["outcome"]
            for event in result["observability_events"]
            if event["event"] == "repo_context_find"
        ]
        assert outcomes[:3] == ["executed", "deduplicated", "deduplicated"]


def test_find_closure_occurs_on_final_permitted_attempt():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle\n")
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)

        for index in range(31):
            assert "matches: 0" in worker._find_command(f"missing-{index}")
        final = worker._find_command("needle")
        packet = closure_packet(final)

        assert "file.txt:1:" in final
        assert "needle (matches=1)" in packet
        assert worker._find_attempts == 32
        assert worker._find_closed is True
        assert worker._find_evidence[-1]["literal"] == "needle"
        assert worker._find_evidence[-1]["execution_order"] == 32
        assert worker._find_command("attempt-33") == adapter._FIND_CLOSED_RESULT
        assert worker._find_attempts == 32
        events = [
            event
            for event in worker._repo_event_entries
            if event["event"] in {"repo_context_find", "repo_context_find_closure"}
        ]
        assert sum(event["event"] == "repo_context_find_closure" for event in events) == 1
        assert not any(event.get("outcome") == "budget_rejected" for event in events)


def test_find_budget_survives_a_soft_reset():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        responses = ["FIND repeated"] * 3
        responses.extend(f"FIND unique-{index}" for index in range(29))
        responses.append(check_task_command())
        result, backend = run_worker(
            workspace, "tools", responses, repeat_action_limit=1
        )

        assert result["status"] == "done"
        events = [
            event
            for event in result["observability_events"]
            if event["event"] == "repo_context_find"
        ]
        assert len(events) == 32
        assert events[-1]["attempt"] == 32
        assert events[-1]["outcome"] == "executed"
        closures = [
            event
            for event in result["observability_events"]
            if event["event"] == "repo_context_find_closure"
        ]
        assert len(closures) == 1
        assert any(
            "did not make progress" in call["messages"][1]["content"]
            for call in backend.call_history
        )


def test_alternating_duplicate_finds_are_bounded_by_budget():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        responses = [
            f"FIND {'alpha' if index % 2 == 0 else 'beta'}"
            for index in range(32)
        ]
        responses.append(check_task_command())
        result, backend = run_worker(
            workspace, "tools", responses, repeat_action_limit=1
        )

        assert result["status"] == "done"
        events = [
            event
            for event in result["observability_events"]
            if event["event"] == "repo_context_find"
        ]
        assert len(events) == 32
        assert events[-1]["attempt"] == 32
        assert events[-1]["outcome"] == "deduplicated"
        assert sum(
            event["event"] == "repo_context_find_closure"
            for event in result["observability_events"]
        ) == 1
        assert not any(
            "did not make progress" in call["messages"][1]["content"]
            for call in backend.call_history
        )


def test_find_only_output_after_closure_stalls_without_another_rejection():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        responses = ["FIND alpha", "FIND beta"]
        result, backend = run_worker(
            workspace, "tools", responses, repo_context_max_finds=1
        )

        assert result["status"] == "stalled"
        assert result["reason"] == "FIND issued after retrieval closure"
        assert len(backend.call_history) == 2
        handoff = backend.call_history[1]["messages"]
        assert "Task T1: Inspect files" in handoff[1]["content"]
        assert "# FIND evidence closure" in handoff[2]["content"]
        assert adapter._EXECUTION_PHASE_INSTRUCTION == handoff[3]["content"]
        assert not any(
            message["content"] == adapter._FIND_CLOSED_RESULT
            for message in handoff
        )
        assert not any(
            event.get("outcome") == "budget_rejected"
            for event in result["observability_events"]
        )
        names = [event["event"] for event in result["observability_events"]]
        assert names.count("repo_context_find_closure") == 1
        assert names.count("repo_context_execution_phase_handoff") == 1
        assert names.count("repo_context_post_handoff_find_stall") == 1


def test_execution_handoff_rebuilds_history_with_feedback_and_drops_find_dialogue():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle\n")
        backend = model.MockBackend([
            text_response("FIND needle"),
            text_response("WRITE file.txt\nnew\nEND WRITE\nDone"),
        ])
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=1),
            1,
            feedback="Reviewer requires the invalid path regression.",
        )

        result = worker.run(backend)

        assert result["status"] == "done"
        assert open(os.path.join(workspace, "file.txt"), encoding="utf-8").read() == "new"
        assert len(backend.call_history) == 2
        initial = backend.call_history[0]["messages"]
        handoff = backend.call_history[1]["messages"]
        assert handoff[0] == initial[0]
        assert [message["role"] for message in handoff] == [
            "system", "user", "user", "user", "user"
        ]
        assert handoff[1]["content"] == "Task T1: Inspect files"
        assert handoff[2]["content"] == (
            "Previous reviewer feedback:\n"
            "Reviewer requires the invalid path regression."
        )
        assert "# FIND evidence closure" in handoff[3]["content"]
        assert handoff[3]["content"] == worker._find_closure_packet
        assert handoff[4]["content"] == adapter._EXECUTION_PHASE_INSTRUCTION
        assert not any(message["role"] == "assistant" for message in handoff)
        assert not any(message["content"] == "FIND needle" for message in handoff)
        assert not any(
            message["content"] == adapter._FIND_CLOSED_RESULT
            for message in handoff
        )

        names = [event["event"] for event in result["observability_events"]]
        assert names.count("repo_context_find_closure") == 1
        assert names.count("repo_context_execution_phase_handoff") == 1
        assert "repo_context_post_handoff_find_stall" not in names


def test_find_closed_mixed_command_executes_write():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle\n")
        result, backend = run_worker(
            workspace,
            "tools",
            [
                "FIND needle",
                "FIND after-closure\nWRITE file.txt\nnew\nEND WRITE",
                "Done",
            ],
            repo_context_max_finds=1,
        )

        assert result["status"] == "done"
        assert open(os.path.join(workspace, "file.txt"), encoding="utf-8").read() == "new"
        handoff = backend.call_history[1]["messages"]
        assert handoff[-1]["content"] == adapter._EXECUTION_PHASE_INSTRUCTION
        assert not any(
            message["content"] == adapter._FIND_CLOSED_RESULT
            for call in backend.call_history
            for message in call["messages"]
        )


def test_soft_reset_preserves_execution_phase_and_closed_find():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "content\n")
        backend = model.MockBackend([
            text_response("FIND needle"),
            text_response("READ file.txt"),
            text_response("READ file.txt"),
            text_response("FIND reopened"),
        ])
        worker = adapter.WorkerAdapter(
            worker_config(
                workspace,
                "tools",
                repo_context_max_finds=1,
                repeat_action_limit=1,
            ),
            1,
        )

        result = worker.run(backend)

        assert result["status"] == "stalled"
        assert result["reason"] == "FIND issued after retrieval closure"
        assert len(backend.call_history) == 4
        final_history = backend.call_history[-1]["messages"]
        assert any(
            message["content"] == adapter._EXECUTION_PHASE_INSTRUCTION
            for message in final_history
        )
        assert any(
            message["content"] == worker._find_closure_packet
            for message in final_history
        )
        assert not any(
            message["content"] == adapter._REPO_CONTEXT_NOTICE
            for message in final_history
        )
        assert worker._find_closed is True
        assert worker._find_attempts == 1
        names = [event["event"] for event in result["observability_events"]]
        assert names.count("repo_context_find_closure") == 1
        assert names.count("repo_context_execution_phase_handoff") == 1
        assert names.count("repo_context_post_handoff_find_stall") == 1


def test_new_worker_gets_a_fresh_find_phase():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle\n")
        first = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=1), 1
        )
        assert "# FIND evidence closure" in first._find_command("needle")
        assert first._find_closed
        assert first._execution_phase_handoff_done is False

        second = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=1), 1
        )
        assert second._find_closed is False
        assert second._execution_phase_handoff_done is False
        assert "matches: 1" in second._find_command("needle")
        assert second._find_attempts == 1


def test_find_closure_deduplicates_coordinates_and_zero_literals():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle here\n")
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=4), 1
        )

        worker._find_command("needle")
        worker._find_command("nee")
        worker._find_command("missing")
        final = worker._find_command("missing")
        packet = closure_packet(final)

        assert packet.count("file.txt:1:") == 1
        assert "needle (matches=1)" in packet
        assert "nee (matches=1)" in packet
        assert packet.count("literal=missing matches=0") == 1
        assert final.count("# FIND evidence closure") == 1


def test_find_closure_sorts_coordinates_and_excludes_stale_fingerprints():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "z.txt", "needle\n")
        write_file(workspace, "a.txt", "needle\n")
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=3), 1
        )

        worker._find_command("needle")
        worker._find_command("z")
        ordered_packet = closure_packet(worker._find_command("a"))
        coordinates = [
            line
            for line in ordered_packet.splitlines()
            if line.startswith("- ") and ":1: literals=" in line
        ]
        assert coordinates[0].startswith("- a.txt:1:")
        assert coordinates[1].startswith("- z.txt:1:")

        write_file(workspace, "z.txt", "needle\n")
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=3), 1
        )
        worker._find_command("needle")
        write_file(workspace, "z.txt", "prefix\nneedle\n")
        worker._find_command("needle")
        stale_packet = closure_packet(worker._find_command("missing"))
        assert "stale evidence excluded=1" in stale_packet
        assert "z.txt:1:" not in stale_packet
        assert "z.txt:2:" in stale_packet


def test_find_closure_bound_truncates_complete_escaped_coordinate_lines():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "00-control.txt", "needle\x07\n")
        for index in range(1, 14):
            write_file(workspace, f"{index:02d}.txt", "needle\n")
        worker = adapter.WorkerAdapter(
            worker_config(
                workspace,
                "tools",
                repo_context_max_finds=3,
                repo_context_find_closure_max_chars=500,
            ),
            1,
        )

        worker._find_command("needle")
        worker._find_command("other")
        packet = closure_packet(worker._find_command("missing"))
        closure_event = next(
            event
            for event in worker._repo_event_entries
            if event["event"] == "repo_context_find_closure"
        )

        assert len(packet) <= 500
        assert packet.endswith(adapter._FIND_CLOSURE_TRUNCATION)
        assert "00-control.txt:1:" in packet
        assert "\\u0007" in packet
        assert "\x07" not in packet
        assert all(
            "excerpt=" in line
            for line in packet.splitlines()
            if line.startswith("- ") and ":1: literals=" in line
        )
        assert closure_event["packet_length"] == len(packet)
        assert closure_event["packet_sha256"] == hashlib.sha256(
            packet.encode("utf-8")
        ).hexdigest()
        assert closure_event["truncated"] is True
        assert "packet" not in closure_event


def test_find_closure_refresh_failure_excludes_stale_evidence_and_continues():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "needle\n")
        worker = adapter.WorkerAdapter(
            worker_config(workspace, "tools", repo_context_max_finds=1), 1
        )
        original_refresh = worker._repo_context.refresh
        calls = 0

        def fail_closure_refresh():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("closure refresh failed")
            return original_refresh()

        worker._repo_context.refresh = fail_closure_refresh
        backend = model.MockBackend([
            text_response("FIND needle"),
            text_response("WRITE file.txt\nnew\nEND WRITE\nDone"),
        ])
        result = worker.run(backend)

        assert result["status"] == "done"
        assert open(os.path.join(workspace, "file.txt"), encoding="utf-8").read() == "new"
        closure_messages = [
            message["content"]
            for call in backend.call_history
            for message in call["messages"]
            if message["role"] == "user" and "# FIND evidence closure" in message["content"]
        ]
        assert len(closure_messages) == 1
        packet = closure_packet(closure_messages[0])
        assert "final repository refresh failed; stale evidence omitted" in packet
        assert "file.txt:1:" not in packet
        assert worker._find_closed is True


def test_find_zero_guidance_and_literal_regex_characters():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        write_file(workspace, "file.txt", "class.*Exception\n")
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)

        literal = worker._find_command("class.*Exception")
        assert "file.txt:1:" in literal
        assert "matches: 1" in literal

        zero = worker._find_command('"class.*Missing"')
        assert "matches: 0" in zero
        assert "FIND is fixed-literal, not regex." in zero
        assert "Characters such as . * [ ] ^ $ and quotes are searched literally." in zero
        assert worker._last_find_useful is True

        duplicate_zero = worker._find_command('"class.*Missing"')
        assert "unchanged:" in duplicate_zero
        assert "FIND is fixed-literal, not regex." not in duplicate_zero

        quoted = worker._find_command('"class.*Exception"')
        assert "matches: 0" in quoted
        assert worker._repo_event_entries[-1]["outcome"] == "executed"


def test_duplicate_find_escapes_controls_and_hides_query_from_events():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        worker = adapter.WorkerAdapter(worker_config(workspace, "tools"), 1)
        query = "/tmp/host\x00\r\n" + ("query" * 80)
        worker._find_command(query)
        duplicate = worker._find_command(query)
        query_line = next(line for line in duplicate.splitlines() if line.startswith("query:"))
        event = worker._repo_event_entries[-1]

        assert "\\x00" in query_line
        assert "\\x0d" in query_line
        assert "\\x0a" in query_line
        assert len(duplicate) <= 300
        assert "\x00" not in query_line
        assert "\r" not in query_line
        assert "\n" not in query_line
        assert "query" not in event
        assert "query_hash" in event
        assert "/tmp/host" not in str(event)
        assert "FIND is exhaustive" not in str(event)


def test_off_mode_does_not_enable_find_ledger_or_budget():
    with tempfile.TemporaryDirectory() as workspace:
        prepare_worker(workspace)
        result, backend = run_worker(
            workspace,
            "off",
            ["FIND missing", check_task_command()],
            repo_context_max_finds=0,
        )
        assert result["status"] == "done"
        assert not any(
            message["content"] == adapter._EXECUTION_PHASE_INSTRUCTION
            for call in backend.call_history
            for message in call["messages"]
        )
