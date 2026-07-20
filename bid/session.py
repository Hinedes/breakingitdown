import json
import os
from .tools import reset_write_tracking
from . import todo as todo_mod


def run_session(prompt_text, assignment, tools, backend, config, workspace, role, worker_number=None):
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": assignment},
    ]
    tool_defs = [t["definition"] for t in tools]
    max_turns = config.get("max_turns", 50)
    max_tokens = config.get("max_tokens")
    reset_write_tracking(workspace)

    for turn in range(max_turns):
        response = backend.run(messages, tool_defs, max_tokens=max_tokens)
        tool_calls = response.get("tool_calls")
        content = response.get("content")

        if tool_calls:
            raw_assistant = {"role": "assistant", "content": content}
            if tool_calls:
                raw_assistant["tool_calls"] = tool_calls
            messages.append(raw_assistant)

            had_finish = False
            finish_summary = ""
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}

                if name == "finish":
                    had_finish = True
                    finish_summary = args.get("summary", "")
                    continue

                tool_obj = next((t for t in tools if t["name"] == name), None)
                if not tool_obj:
                    from .tools import TOOL_ALIASES
                    aliased = TOOL_ALIASES.get(name)
                    if aliased:
                        tool_obj = next((t for t in tools if t["name"] == aliased), None)
                    if not tool_obj:
                        known = ", ".join(t["name"] for t in tools[:8])
                        result = f"unknown tool: {name}. Available: {known}"

                if tool_obj:
                    try:
                        result = tool_obj["handler"](args, workspace, role, worker_number)
                    except Exception as e:
                        result = f"error executing {name}: {e}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

            # Check if Worker's task just became checked
            if role != "manager" and worker_number is not None:
                todo_path = os.path.join(workspace, "docs/todo.md")
                if os.path.exists(todo_path):
                    with open(todo_path) as f:
                        tasks = todo_mod.parse_todo(f.read())
                    task = todo_mod.get_task(tasks, worker_number)
                    if task and task["checked"]:
                        return {
                            "status": "success",
                            "summary": f"T{worker_number} submitted",
                            "turn_count": turn + 1,
                        }

            if had_finish:
                return {
                    "status": "success",
                    "summary": finish_summary or "completed",
                    "turn_count": turn + 1,
                }
            continue

        text = (content or "").strip()
        if not text:
            break

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": "Use tools. Output JSON only."})

    return {"status": "error", "reason": "turn limit exceeded"}
