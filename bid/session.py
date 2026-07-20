import json


_ERROR_PREFIXES = (
    "error:",
    "unknown tool:",
    "permission denied:",
    "path not found:",
    "not a directory:",
    "file not found:",
    "not a file:",
    "old_text not found",
)

_OP_LEDGER_MAX = 20


def _tool_succeeded(result):
    text = str(result).strip().lower()
    return not any(text.startswith(prefix) for prefix in _ERROR_PREFIXES)


def _compact_ledger(ledger, count):
    if len(ledger) <= count:
        return ledger
    # Keep the most recent `count` entries, prefix with a summary
    kept = ledger[-count:]
    summary = f"[{len(ledger) - count} earlier operations omitted]"
    return [summary] + kept


def run_turn(messages, tools, backend, config, workspace, role, worker_number=None):
    tool_defs = [tool["definition"] for tool in tools]
    max_tokens = config.get("max_tokens")
    ledger = config.get("_op_ledger", [])

    response = backend.run(messages, tool_defs, max_tokens=max_tokens)
    tool_calls = response.get("tool_calls") or []
    content = response.get("content")
    transport = response.get("tool_transport", "native")
    events = []

    if tool_calls and transport == "text":
        # Text transport: append only as assistant content + user result.
        # Never use assistant.tool_calls or role="tool".
        raw_text = response.get("_raw_content") or content or ""
        assistant_msg = {"role": "assistant", "content": raw_text}
        messages.append(assistant_msg)

        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            raw_arguments = call.get("function", {}).get("arguments", "{}")
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
            except (json.JSONDecodeError, TypeError, ValueError):
                arguments = {}
                result = "error: malformed tool arguments"
            else:
                tool_obj = next((tool for tool in tools if tool["name"] == name), None)
                if not tool_obj:
                    from .tools import TOOL_ALIASES
                    alias = TOOL_ALIASES.get(name)
                    if alias:
                        tool_obj = next((tool for tool in tools if tool["name"] == alias), None)
                        if tool_obj:
                            name = alias
                if not tool_obj:
                    known = ", ".join(tool["name"] for tool in tools)
                    result = f"unknown tool: {name}. Available: {known}"
                else:
                    try:
                        result = tool_obj["handler"](arguments, workspace, role, worker_number)
                    except Exception as exc:
                        result = f"error: {name} raised {exc}"

            event = {
                "name": name,
                "arguments": arguments,
                "result": str(result),
                "success": _tool_succeeded(result),
            }
            events.append(event)

            # Append result as a user message
            messages.append({"role": "user", "content": f"[{name} result]\n{result}"})

        # Compact ledger: keep recent ops, drop full file contents for old ones
        if events:
            for ev in events:
                entry = f"{ev['name']}: {'ok' if ev['success'] else 'fail'}"
                ledger.append(entry)
            ledger = _compact_ledger(ledger, _OP_LEDGER_MAX)

    elif tool_calls:
        # Native transport: preserve OpenAI tool transcript
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })

        for call in tool_calls:
            name = call.get("function", {}).get("name", "")
            raw_arguments = call.get("function", {}).get("arguments", "{}")
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
            except (json.JSONDecodeError, TypeError, ValueError):
                arguments = {}
                result = "error: malformed tool arguments"
            else:
                tool_obj = next((tool for tool in tools if tool["name"] == name), None)
                if not tool_obj:
                    from .tools import TOOL_ALIASES
                    alias = TOOL_ALIASES.get(name)
                    if alias:
                        tool_obj = next((tool for tool in tools if tool["name"] == alias), None)
                        if tool_obj:
                            name = alias
                if not tool_obj:
                    known = ", ".join(tool["name"] for tool in tools)
                    result = f"unknown tool: {name}. Available: {known}"
                else:
                    try:
                        result = tool_obj["handler"](arguments, workspace, role, worker_number)
                    except Exception as exc:
                        result = f"error: {name} raised {exc}"

            event = {
                "name": name,
                "arguments": arguments,
                "result": str(result),
                "success": _tool_succeeded(result),
            }
            events.append(event)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", f"call_{name}"),
                "content": str(result),
            })

    elif content is not None:
        messages.append({"role": "assistant", "content": content})

    return {
        "messages": messages,
        "content": (content or "").strip(),
        "tool_calls": bool(tool_calls),
        "tool_events": events,
        "_op_ledger": ledger,
    }


def run_session(prompt_text, assignment, tools, backend, config, workspace, role, worker_number=None):
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": assignment},
    ]
    return run_turn(messages, tools, backend, config, workspace, role, worker_number)
