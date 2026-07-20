import os
import json

from . import permissions
from . import todo as todo_mod


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
    for e in sorted(os.listdir(abs_path)):
        epath = os.path.join(abs_path, e)
        if os.path.isdir(epath):
            entries.append(f"{e}/")
        else:
            entries.append(e)
    return "\n".join(entries) if entries else "(empty)"


def handle_read_file(args, workspace, role, worker_number):
    path = args.get("path", "")
    if not path:
        return "error: path required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    abs_path = os.path.join(workspace, rel)
    if not os.path.exists(abs_path):
        return f"file not found: {path}"
    if not os.path.isfile(abs_path):
        return f"not a file: {path}"
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"error reading file: {e}"


_last_write_paths = {}

def reset_write_tracking(workspace):
    for key in list(_last_write_paths.keys()):
        if key[0] == workspace:
            del _last_write_paths[key]


def handle_write_file(args, workspace, role, worker_number):
    path = args.get("path", "")
    content = args.get("content", "")
    if not path:
        return "error: path required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    allowed, err_msg = permissions.check_write_permission(rel, role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"
    abs_path = os.path.join(workspace, rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    
    # Check if this is a repeated write to the same path in the same session
    key = (workspace, rel)
    mode = "w"
    if _last_write_paths.get(key) and os.path.exists(abs_path):
        mode = "a"
    _last_write_paths[key] = True
    
    try:
        with open(abs_path, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write("\n")
            f.write(content)
        return f"wrote {len(content)} bytes to {rel} (mode={mode})"
    except Exception as e:
        return f"error writing file: {e}"


TOOL_ALIASES = {
    "create_file": "write_file", "new_file": "write_file", "save_file": "write_file",
    "complete_task": "check_own_task", "mark_done": "check_own_task",
    "submit_task": "check_own_task", "done": "check_own_task",
}


def handle_replace_text(args, workspace, role, worker_number):
    path = args.get("path", "")
    old_text = args.get("old_text", "")
    new_text = args.get("new_text", "")
    if not path or not old_text:
        return "error: path and old_text required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    allowed, err_msg = permissions.check_write_permission(rel, role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"
    abs_path = os.path.join(workspace, rel)
    if not os.path.exists(abs_path):
        return f"file not found: {path}"
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return f"old_text not found in {rel}"
        new_content = content.replace(old_text, new_text, 1)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"replaced text in {rel}"
    except Exception as e:
        return f"error replacing text: {e}"


def handle_make_directory(args, workspace, role, worker_number):
    path = args.get("path", "")
    if not path:
        return "error: path required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    allowed, err_msg = permissions.check_write_permission(rel + "/.keep", role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"
    abs_path = os.path.join(workspace, rel)
    try:
        os.makedirs(abs_path, exist_ok=True)
        return f"created directory {rel}"
    except Exception as e:
        return f"error creating directory: {e}"


def handle_delete_file(args, workspace, role, worker_number):
    path = args.get("path", "")
    if not path:
        return "error: path required"
    safe, err, rel = permissions.check_path_safety(path, workspace)
    if not safe:
        return err
    allowed, err_msg = permissions.check_write_permission(rel, role, worker_number)
    if not allowed:
        return f"permission denied: {err_msg}"
    abs_path = os.path.join(workspace, rel)
    if not os.path.exists(abs_path):
        return f"not found: {path}"
    try:
        if os.path.isdir(abs_path):
            import shutil
            shutil.rmtree(abs_path)
            return f"deleted directory {rel}"
        else:
            os.remove(abs_path)
            return f"deleted file {rel}"
    except Exception as e:
        return f"error deleting: {e}"


def handle_check_own_task(args, workspace, role, worker_number):
    if role != permissions.ROLE_WORKER:
        return "permission denied: only workers can check own task"
    todo_path = os.path.join(workspace, "docs/todo.md")
    if not os.path.exists(todo_path):
        return "error: todo.md not found"
    with open(todo_path, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = todo_mod.set_task_checked(content, worker_number, checked=True)
    if new_content == content:
        return f"error: task T{worker_number} not found in todo.md"
    with open(todo_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return f"checked T{worker_number} in todo.md"


def handle_finish(args, workspace, role, worker_number):
    summary = args.get("summary", "")
    if role == permissions.ROLE_MANAGER:
        return "__FINISH__" + summary
    return f"finish ignored — use check_own_task to submit"


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
                    "properties": {k: v for k, v in params.items()},
                    "required": [k for k, v in params.items() if v.get("required")],
                }
            }
        },
        "handler": handler,
    }


def param(type, description, required=True):
    return {"type": type, "description": description, "required": required}


COMMON_TOOLS = [
    make_tool("list_files", "List files and directories at a path relative to workspace root.",
              {"path": param("string", "Path relative to workspace root")},
              handle_list_files),
    make_tool("read_file", "Read the contents of a file relative to workspace root.",
              {"path": param("string", "Path relative to workspace root")},
              handle_read_file),
    make_tool("write_file", "Write content to a file. Creates parent directories if needed.",
              {"path": param("string", "Path relative to workspace root"),
               "content": param("string", "File content")},
              handle_write_file),
    make_tool("replace_text", "Replace first occurrence of old_text with new_text in a file.",
              {"path": param("string", "Path relative to workspace root"),
               "old_text": param("string", "Text to find"),
               "new_text": param("string", "Replacement text")},
              handle_replace_text),
    make_tool("finish", "Signal that work is complete. Provide a summary of what was accomplished.",
              {"summary": param("string", "Summary of accomplishments")},
              handle_finish),
]

MANAGER_ONLY_TOOLS = [
    list(COMMON_TOOLS),
]

WORKER_TOOLS = [
    make_tool("check_own_task", "Mark your own task as completed in docs/todo.md.",
              {},
              handle_check_own_task),
    make_tool("make_directory", "Create a directory relative to workspace root.",
              {"path": param("string", "Directory path relative to workspace root")},
              handle_make_directory),
    make_tool("delete_file", "Delete a file or empty directory.",
              {"path": param("string", "Path relative to workspace root")},
              handle_delete_file),
]


def get_tools_for_role(role, worker_number=None):
    result = list(COMMON_TOOLS)
    if role == permissions.ROLE_WORKER:
        result.extend(WORKER_TOOLS)
    return result
