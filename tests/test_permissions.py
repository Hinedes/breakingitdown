import pytest
import sys
import os
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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


def test_worker_write_allowed():
    allowed, err = permissions.check_write_permission("docs/research/foo.md", permissions.ROLE_WORKER)
    assert allowed


def test_worker_write_blocked():
    for f in permissions.WORKER_BLOCKED:
        allowed, err = permissions.check_write_permission(f, permissions.ROLE_WORKER)
        assert not allowed, f"worker should not write {f}"


def test_worker_can_write_artifacts():
    allowed, err = permissions.check_write_permission("src/main.py", permissions.ROLE_WORKER)
    assert allowed
    allowed, err = permissions.check_write_permission("outputs/results.txt", permissions.ROLE_WORKER)
    assert allowed
