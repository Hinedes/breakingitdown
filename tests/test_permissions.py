import os
import tempfile

from bid import permissions


def _prepare_workspace(tmp):
    os.makedirs(os.path.join(tmp, "docs", "research", "T1"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "research", "T1", "search-001.md"), "w", encoding="utf-8") as file:
        file.write("evidence")


def test_path_safety_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        safe, err, rel = permissions.check_path_safety("docs/todo.md", tmp)
        assert safe
        assert rel == "docs/todo.md"


def test_path_traversal_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        safe, err, rel = permissions.check_path_safety("../../../etc/passwd", tmp)
        assert not safe
        assert "traversal" in err


def test_absolute_path_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        safe, err, rel = permissions.check_path_safety("/etc/passwd", tmp)
        assert not safe
        assert "traversal" in err


def test_worker_can_write_non_control_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert permissions.check_write_permission("notes/result.md", permissions.ROLE_WORKER, 1, tmp)[0]


def test_worker_cannot_write_control_files():
    with tempfile.TemporaryDirectory() as tmp:
        for path in ("docs/todo.md", "docs/project-status.md", ".bid/current", "docs/reviews/T1.md"):
            allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1, tmp)
            assert not allowed, path


def test_worker_can_read_research_but_not_control_paths():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_workspace(tmp)
        assert permissions.check_read_permission("docs/research/T1/search-001.md", permissions.ROLE_WORKER, 1, tmp)[0]
        for path in ("docs/todo.md", "docs/task.md", "docs/project-status.md", ".bid/current"):
            allowed, err = permissions.check_read_permission(path, permissions.ROLE_WORKER, 1, tmp)
            assert not allowed, path


def test_manager_write_allowed():
    allowed, err = permissions.check_write_permission("docs/todo.md", permissions.ROLE_MANAGER)
    assert allowed
