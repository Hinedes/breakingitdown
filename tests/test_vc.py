import pytest
import sys
import os
import tempfile
import multiprocessing
import time
from tests import open_utf8 as open
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid import vc as vc_mod


def _hold_vc_lock(workspace, ready):
    store = vc_mod.VersionControl(workspace)
    store._acquire_lock()
    ready.set()
    time.sleep(30)


@pytest.fixture
def ws():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs"))
        with open(os.path.join(tmp, "docs", "hello.txt"), "w") as f:
            f.write("hello")
        yield tmp


def test_init_creates_bid_dir(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    assert os.path.exists(os.path.join(ws, ".bid", "current"))
    assert os.path.exists(os.path.join(ws, ".bid", "log.md"))
    assert os.path.exists(os.path.join(ws, ".bid", "states", "s0"))


def test_get_current(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    assert vc.get_current() == "s0"


def test_save_state_creates_new(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    name = vc.save_state("Worker 1", "did work")
    assert name == "s1"
    assert vc.get_current() == "s1"
    assert os.path.isdir(os.path.join(ws, ".bid", "states", "s1"))


def test_restore_rolls_back(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    with open(os.path.join(ws, "docs", "hello.txt"), "w") as f:
        f.write("modified")
    vc.save_state("test", "modified state")
    # Now restore s0
    vc.restore("s0")
    assert vc.get_current() == "s0"
    with open(os.path.join(ws, "docs", "hello.txt")) as f:
        assert f.read() == "hello"
    assert not os.path.isdir(os.path.join(ws, ".bid", "states", "s1"))


def test_restore_workspace_keeps_append_only_history(ws):
    store = vc_mod.VersionControl(ws)
    store.init()
    store.save_state("Worker", "later")

    store.restore_workspace("s0")

    assert store.get_current() == "s1"
    assert os.path.isdir(os.path.join(ws, ".bid", "states", "s1"))
    assert "Worker completed." in store.get_log()


def test_os_lock_blocks_live_owner_and_releases_on_process_exit(ws, monkeypatch):
    store = vc_mod.VersionControl(ws)
    store.init()
    monkeypatch.setattr(vc_mod, "_LOCK_TIMEOUT", 0.3)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    owner = context.Process(target=_hold_vc_lock, args=(ws, ready))
    owner.start()
    try:
        assert ready.wait(10), "lock owner did not acquire lock"
        with pytest.raises(RuntimeError, match="could not acquire VC lock"):
            vc_mod.VersionControl(ws).append_log("blocked")
        assert owner.is_alive()
    finally:
        owner.terminate()
        owner.join(10)

    vc_mod.VersionControl(ws).append_log("after process exit")
    assert "after process exit" in store.get_log()
    assert os.path.isfile(os.path.join(ws, ".bid", "lock"))


def test_log_entries(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    vc.save_state("Worker 1", "did something")
    log = vc.get_log()
    assert "Worker 1" in log
    assert "s0" in log
    assert "s1" in log


def test_restore_nonexistent(ws):
    vc = vc_mod.VersionControl(ws)
    vc.init()
    with pytest.raises(ValueError):
        vc.restore("s99")
