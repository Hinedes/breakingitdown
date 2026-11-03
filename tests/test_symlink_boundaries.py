import builtins
import errno
import os
import pathlib
import shutil

import pytest

from bid import adapter, harness, vc
from bid.observer import Observer


def link(target, path, directory=False):
    try:
        os.symlink(target, path, target_is_directory=directory)
    except NotImplementedError:
        pytest.skip("symlink support unavailable")
    except OSError as exc:
        if exc.errno == errno.ENOSYS or getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink privilege unavailable")
        raise


@pytest.mark.parametrize("restore_method", ["restore", "restore_workspace"])
def test_host_readers_and_snapshots_preserve_links_without_following(tmp_path, monkeypatch, restore_method):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "sentinel.txt"
    secret.write_text("FAKE_SECRET_MUST_NOT_ENTER_EVIDENCE", encoding="utf-8")
    workspace = tmp_path / "workspace"
    harness.ensure_workspace(str(workspace))
    store = vc.VersionControl(str(workspace))
    store.init()
    link(secret, workspace / "leak")
    link(outside, workspace / "directory-link", directory=True)
    link(outside / "missing", workspace / "dangling")

    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if not isinstance(file, int):
            path = pathlib.Path(file)
            assert not path.is_symlink(), "host attempted to open a model-controlled symlink"
            assert not path.resolve().is_relative_to(outside), "host attempted to read outside project"
        return original_open(file, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", guarded_open)
        observer = Observer(str(workspace))
        assert observer._snapshot["leak"].startswith("symlink:")
        assert "directory-link" in observer._snapshot
        candidate = store.save_state("worker", "symlink candidate")
        snapshot = pathlib.Path(store.states_dir) / candidate
        assert (snapshot / "leak").is_symlink()
        assert (snapshot / "directory-link").is_symlink()
        assert (snapshot / "dangling").is_symlink()
        diff = adapter._workspace_diff(str(pathlib.Path(store.states_dir) / "s0"), str(snapshot))
        assert "SYMLINK (target not read)" in diff
        assert "FAKE_SECRET_MUST_NOT_ENTER_EVIDENCE" not in diff
        controls = adapter._snapshot_control_state(str(workspace))
        try:
            assert not adapter._control_state_changed(str(workspace), controls)
        finally:
            shutil.rmtree(controls)
        getattr(store, restore_method)(candidate)
        assert (workspace / "leak").is_symlink()
        assert (workspace / "directory-link").is_symlink()
        assert (workspace / "dangling").is_symlink()
    assert secret.read_text(encoding="utf-8") == "FAKE_SECRET_MUST_NOT_ENTER_EVIDENCE"


@pytest.mark.parametrize("relative", ["docs", "docs/task.md", ".bid"])
def test_control_symlinks_are_rejected_before_initialization_writes(tmp_path, relative):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    link(sentinel if relative.endswith(".md") else outside, path, directory=not relative.endswith(".md"))
    with pytest.raises(ValueError, match="symlink"):
        harness.ensure_workspace(str(workspace))
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named pipes required")
def test_observer_and_reviewer_do_not_open_named_pipes(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    base = tmp_path / "base"
    candidate.mkdir()
    base.mkdir()
    pipe = candidate / "pipe"
    os.mkfifo(pipe)
    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        assert pathlib.Path(file) != pipe, "host would block opening the model-created pipe"
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    assert Observer(str(candidate))._snapshot == {}
    assert "special file; contents not read" in adapter._workspace_diff(str(base), str(candidate))
