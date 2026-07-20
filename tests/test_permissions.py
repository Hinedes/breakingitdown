import os
import tempfile

from bid import permissions


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


def test_manager_write_allowed():
    allowed, err = permissions.check_write_permission("docs/todo.md", permissions.ROLE_MANAGER)
    assert allowed


def test_manager_write_denied():
    allowed, err = permissions.check_write_permission("docs/research/foo.md", permissions.ROLE_MANAGER)
    assert not allowed


def test_worker_can_write_artifacts():
    for path in ("docs/research/foo.md", "src/main.py", "outputs/results.txt"):
        allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1)
        assert allowed


def test_worker_can_request_todo_write_for_content_validation():
    allowed, err = permissions.check_write_permission("docs/todo.md", permissions.ROLE_WORKER, 1)
    assert allowed


def test_worker_instruction_and_manager_files_blocked():
    for path in permissions.WORKER_BLOCKED:
        allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1)
        assert not allowed, f"worker should not write {path}"
