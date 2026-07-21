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
    "docs/project-status.md",
    "docs/decisions.md",
    "docs/manager.md",
    "docs/worker.md",
}


def _path_blocked(rel_path):
    if rel_path == ".bid" or rel_path.startswith(".bid/"):
        return True
    if rel_path == "docs/reviews" or rel_path.startswith("docs/reviews/"):
        return True
    if rel_path == "docs/research" or rel_path.startswith("docs/research/"):
        return True
    if rel_path == "docs/.completed_hash":
        return True
    return False


def check_path_safety(path, workspace_root):
    abs_path = os.path.realpath(os.path.join(workspace_root, path))
    abs_workspace = os.path.realpath(workspace_root)
    if not abs_path.startswith(abs_workspace + os.sep) and abs_path != abs_workspace:
        return False, "path traversal denied", None
    rel = os.path.relpath(abs_path, abs_workspace).replace(os.sep, "/")
    return True, None, rel


def check_write_permission(rel_path, role, worker_number=None):
    if role == ROLE_MANAGER:
        if rel_path in MANAGER_WRITABLE:
            return True, None
        return False, f"manager cannot write {rel_path}"

    if role == ROLE_WORKER:
        if _path_blocked(rel_path):
            return False, f"worker cannot write control path {rel_path}"
        if rel_path == "docs/todo.md":
            return True, None
        if rel_path in WORKER_BLOCKED:
            return False, f"worker cannot modify {rel_path}"
        return True, None

    return False, f"unknown role: {role}"


def check_read_permission(rel_path, role):
    """Workers may READ research dirs; all other controls apply."""
    if role == ROLE_WORKER:
        if rel_path.startswith("docs/research/"):
            return True, None
    return True, None
