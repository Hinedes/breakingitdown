import os

from . import permissions
from . import todo as todo_mod


def _safe_path(args, workspace, key="path"):
    path = args.get(key, "")
    if not path:
        return None, None, "error: path required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return None, None, err
    return path, rel, None


def handle_list_files(args, workspace, role, worker_number):
    path = args.get("path", ".")
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    abs_path = os.path.join(workspace, rel)
    if not os.path.exists(abs_path):
        return f"path not found: {path}"
    if not os.path.isdir(abs_path):
        return f"not a directory: {path}"
    entries = []
    for entry in sorted(os.listdir(abs_path)):
        entry_path = os.path.join(abs_path, entry)
        entries.append(entry + "/" if os.path.isdir(entry_path) else entry)
    return "\n".join(entries) if entries else "(empty)"


def _normalize_paths(args):
    """Accept single path, array, or comma-separated string.  Return list."""
    raw = args.get("path") or args.get("paths") or ""
    if isinstance(raw, list):
        return [str(p) for p in raw]
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(",", "\n").split("\n") if p.strip()]
        if parts:
            return parts
    return []


def handle_read_file(args, workspace, role, worker_number):
    paths = _normalize_paths(args)
    if not paths:
        return "error: path required. Use: {\"path\": \"docs/file.md\"} or {\"paths\": [\"a.md\", \"b.md\"]}"
    results = []
    for path in paths:
        safe, err, rel = permissions.check_path_safety(path, workspace)
        if not safe:
            results.append(f"error: {err}")
            continue
        abs_path = os.path.join(workspace, rel)
        if not os.path.exists(abs_path):
            results.append(f"file not found: {path}")
            continue
        if not os.path.isfile(abs_path):
            results.append(f"not a file: {path}")
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as file:
                results.append(f"--- {rel} ---\n{file.read()}")
        except Exception as exc:
            results.append(f"error reading {rel}: {exc}")
    return "\n\n".join(results)


def handle_write_file(args, workspace, role, worker_number):
    path, rel, error = _safe_path(args, workspace)
    if error:
        return error
    content = args.get("content")
    if not isinstance(content, str):
        return "error: content must be a string"

    allowed, err_msg = permissions.check_write_permission(rel, role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"

    abs_path = os.path.join(workspace, rel)
    if role == permissions.ROLE_WORKER and rel == "docs/todo.md":
        if not os.path.exists(abs_path):
            return "error: todo.md not found"
        with open(abs_path, "r", encoding="utf-8") as file:
            current = file.read()
        valid, reason = todo_mod.validate_worker_todo_update(current, content, worker_number)
        if not valid:
            return f"permission denied: {reason}"

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as file:
            file.write(content)
        return f"wrote {len(content)} bytes to {rel}"
    except Exception as exc:
        return f"error writing file: {exc}"


def handle_replace_text(args, workspace, role, worker_number):
    path, rel, error = _safe_path(args, workspace)
    if error:
        return error
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    if not old_text:
        return "error: old_text required"

    allowed, err_msg = permissions.check_write_permission(rel, role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"

    abs_path = os.path.join(workspace, rel)
    if not os.path.exists(abs_path):
        return f"file not found: {path}"
    try:
        with open(abs_path, "r", encoding="utf-8") as file:
            current = file.read()
        if old_text not in current:
            return f"old_text not found in {rel}"
        updated = current.replace(old_text, new_text, 1)
        if role == permissions.ROLE_WORKER and rel == "docs/todo.md":
            valid, reason = todo_mod.validate_worker_todo_update(current, updated, worker_number)
            if not valid:
                return f"permission denied: {reason}"
        with open(abs_path, "w", encoding="utf-8") as file:
            file.write(updated)
        return f"replaced text in {rel}"
    except Exception as exc:
        return f"error replacing text: {exc}"


def make_tool(name, description, params, handler):
    return {
        "name": name,
        "definition": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": dict(params),
                    "required": [key for key, value in params.items() if value.get("required")],
                },
            },
        },
        "handler": handler,
    }


def param(kind, description, required=True):
    return {"type": kind, "description": description, "required": required}


BASIC_TOOLS = [
    make_tool(
        "read_file",
        "Read a UTF-8 text file inside the workspace.",
        {"path": param("string", "Workspace-relative file path")},
        handle_read_file,
    ),
    make_tool(
        "write_file",
        "Overwrite a UTF-8 text file inside the workspace.",
        {
            "path": param("string", "Workspace-relative file path"),
            "content": param("string", "Complete file content"),
        },
        handle_write_file,
    ),
    make_tool(
        "replace_text",
        "Replace one exact text occurrence in a workspace file.",
        {
            "path": param("string", "Workspace-relative file path"),
            "old_text": param("string", "Exact text to replace"),
            "new_text": param("string", "Replacement text"),
        },
        handle_replace_text,
    ),
]

TOOL_ALIASES = {
    "create_file": "write_file",
    "new_file": "write_file",
    "save_file": "write_file",
}


def get_tools_for_role(role, worker_number=None):
    return list(BASIC_TOOLS)
