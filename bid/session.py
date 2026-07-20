import json
from .tools import reset_write_tracking


def run_turn(messages, tools, backend, config, workspace, role, worker_number=None):
    tool_defs = [t["definition"] for t in tools]
    max_tokens = config.get("max_tokens")

    response = backend.run(messages, tool_defs, max_tokens=max_tokens)
    tool_calls = response.get("tool_calls")
    content = response.get("content")

    if tool_calls:
        raw_assistant = {"role": "assistant", "content": content}
        if tool_calls:
            raw_assistant["tool_calls"] = tool_calls
        messages.append(raw_assistant)

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}

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

    return {
        "messages": messages,
        "content": (content or "").strip(),
        "tool_calls": bool(tool_calls),
    }


def run_session(prompt_text, assignment, tools, backend, config, workspace, role, worker_number=None):
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": assignment},
    ]
    reset_write_tracking(workspace)
    return run_turn(messages, tools, backend, config, workspace, role, worker_number)
