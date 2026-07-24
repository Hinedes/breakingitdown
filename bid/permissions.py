import os

ROLE_MANAGER = "manager"
ROLE_WORKER = "worker"

MANAGER_WRITABLE = {
    "docs/task.md",
    "docs/todo.md",
    "docs/project-status.md",
    "docs/decisions.md",
}

WORKER_BLOCKED = {
    "docs/task.md",
    "docs/todo.md",
    "docs/project-status.md",
    "docs/decisions.md",
    "docs/manager.md",
    "docs/worker.md",
    "docs/reviews",
    ".bid",
}


def _is_blocked_path(rel_path):
    if rel_path in WORKER_BLOCKED:
        return True
    return any(
        rel_path.startswith(prefix + "/")
        for prefix in (".bid", "docs/reviews")
    )


def _read_blocked(rel_path):
    return _is_blocked_path(rel_path)


def check_path_safety(path, workspace_root):
    abs_path = os.path.realpath(os.path.join(workspace_root, path))
    abs_workspace = os.path.realpath(workspace_root)
    if not abs_path.startswith(abs_workspace + os.sep) and abs_path != abs_workspace:
        return False, "path traversal denied", None
    rel = os.path.relpath(abs_path, abs_workspace).replace(os.sep, "/")
    return True, None, rel


def check_write_permission(rel_path, role, worker_number=None, workspace=None):
    if role == ROLE_MANAGER:
        if rel_path in MANAGER_WRITABLE:
            return True, None
        return False, f"manager cannot write {rel_path}"

    if role == ROLE_WORKER:
        if _is_blocked_path(rel_path):
            return False, f"worker cannot write control path {rel_path}"
        return True, None

    return False, f"unknown role: {role}"


def check_read_permission(rel_path, role, worker_number=None, workspace=None):
    if role == ROLE_WORKER:
        if _read_blocked(rel_path):
            return False, f"worker cannot read control path {rel_path}"
    return True, None
