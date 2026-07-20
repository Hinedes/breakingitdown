import json


def run_session(prompt_text, assignment, tools, backend, config, workspace, role, worker_number=None):
    messages = [
        {"role": "system", "content": prompt_text},
        {"role": "user", "content": assignment},
    ]
    tool_defs = [t["definition"] for t in tools]
    max_turns = config.get("max_turns", 50)
    max_tokens = config.get("max_tokens")

    for turn in range(max_turns):
        response = backend.run(messages, tool_defs, max_tokens=max_tokens)
        tool_calls = response.get("tool_calls")

        if tool_calls:
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
                else:
                    try:
                        result = tool_obj["handler"](args, workspace, role, worker_number)
                    except Exception as e:
                        result = f"error executing {name}: {e}"

                finish_marker = "__FINISH__"
                if isinstance(result, str) and result.startswith(finish_marker):
                    return {
                        "status": "success",
                        "summary": result[len(finish_marker):],
                        "turn_count": turn + 1,
                    }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": str(result),
                })

            if had_finish:
                return {
                    "status": "success",
                    "summary": finish_summary or "completed",
                    "turn_count": turn + 1,
                }

            if turn == 0:
                messages.append({"role": "user", "content": assignment})
            continue

        text = (response.get("content") or "").strip()
        if not text:
            break

        messages.append({"role": "assistant", "content": response["content"]})
        if turn == 0:
            messages.append({"role": "user", "content": "Use tools. Output JSON only."})

    return {"status": "error", "reason": "turn limit exceeded"}
