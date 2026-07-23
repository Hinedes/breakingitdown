import os
import tempfile

from bid import permissions


def _prepare_worker_todo(tmp, todo_text="- [ ] T1 — Write result\n"):
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    with open(os.path.join(tmp, "docs", "todo.md"), "w", encoding="utf-8") as file:
        file.write(todo_text)


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


def test_worker_can_write_assigned_output_only():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_worker_todo(tmp, "- [ ] T1 — Write result\n  Output: notes/result.md\n  Inputs: docs/task.md\n  Accept: result file exists\n")
        allowed, err = permissions.check_write_permission("notes/result.md", permissions.ROLE_WORKER, 1, tmp)
        assert allowed, err


def test_worker_cannot_write_other_files():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_worker_todo(tmp, "- [ ] T1 — Write result\n  Output: notes/result.md\n  Inputs: docs/task.md\n  Accept: result file exists\n")
        for path in ("src/main.py", "outputs/results.txt", "docs/work/T1.md", "docs/work/T2.md"):
            allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1, tmp)
            assert not allowed, f"{path} should be blocked"


def test_worker_without_output_is_denied():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_worker_todo(tmp, "- [ ] T1 — Write result\n  Inputs: docs/task.md\n  Accept: result file exists\n")
        allowed, err = permissions.check_write_permission("docs/work/T1.md", permissions.ROLE_WORKER, 1, tmp)
        assert not allowed


def test_worker_can_read_safe_file_outside_inputs():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_worker_todo(tmp, "- [ ] T1 — Read research\n  Output: notes/result.md\n  Inputs: docs/research/T1/\n  Accept: result file exists\n")
        allowed, err = permissions.check_read_permission("src/standalone.txt", permissions.ROLE_WORKER, 1, tmp)
        assert allowed, err


def test_worker_cannot_read_private_control_paths():
    with tempfile.TemporaryDirectory() as tmp:
        _prepare_worker_todo(tmp, "- [ ] T1 — Read research\n  Output: notes/result.md\n  Inputs: docs/research/T1/\n  Accept: result file exists\n")
        for path in (".bid/current", "docs/reviews/T1.md", "docs/project-status.md", "docs/.completed_hash"):
            allowed, err = permissions.check_read_permission(path, permissions.ROLE_WORKER, 1, tmp)
            assert not allowed, f"{path} should be blocked"

def test_worker_cannot_write_research():
    for path in ("docs/research", "docs/research/T1/search-001.md"):
        allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1)
        assert not allowed, f"{path} should be blocked"


def test_worker_can_request_todo_write_for_content_validation():
    allowed, err = permissions.check_write_permission("docs/todo.md", permissions.ROLE_WORKER, 1)
    assert allowed


def test_worker_instruction_and_manager_files_blocked():
    for path in permissions.WORKER_BLOCKED:
        allowed, err = permissions.check_write_permission(path, permissions.ROLE_WORKER, 1)
        assert not allowed, f"worker should not write {path}"
