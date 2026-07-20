import json
import re

class ModelBackend:
    def run(self, messages, tools, max_tokens=None):
        raise NotImplementedError


class LlamaCppBackend(ModelBackend):
    def __init__(self, endpoint="http://127.0.0.1:8080/v1/chat/completions",
                 model="smollm3-3b", timeout=120, text_tools=False,
                 max_tokens=256):
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.text_tools = text_tools
        self.max_tokens = max_tokens

    def _build_text_tool_system_message(self, messages, tools):
        if not tools:
            return messages
        if messages and messages[0]["role"] == "system":
            sys_content = messages[0]["content"]
            if "Available tools:" in sys_content:
                return messages
        
        lines = ["Tools:"]
        for t in tools:
            lines.append("- " + t["function"]["name"])
        lines.append("")
        lines.append('Output JSON. Example: {"tool_calls":[{"tool":"write_file","arguments":{"path":"x","content":"y"}},{"tool":"finish","arguments":{"summary":"d"}}]}')
        lines.append("Never narrate.")
        extra = "\n".join(lines)
        new_messages = []
        for msg in messages:
            if msg["role"] == "system":
                new_messages.append(
                    {"role": "system", "content": msg["content"] + "\n\n" + extra}
                )
            else:
                new_messages.append(msg)
        return new_messages

    KNOWN_TOOLS = {"list_files", "read_file", "write_file", "replace_text",
                    "make_directory", "delete_file", "finish",
                    "create_file", "new_file", "save_file"}

    def _parse_one_tool_obj(self, obj):
        if not isinstance(obj, dict):
            return None
        tc_list = obj.get("tool_calls") or obj.get("toolCalls")
        if isinstance(tc_list, list):
            results = []
            for tc in tc_list:
                name = tc.get("tool") or tc.get("name") or ""
                args = tc.get("arguments") or tc.get("parameters") or {}
                if name:
                    results.append({"name": name, "arguments": json.dumps(args)})
            return results
        name = obj.get("tool") or obj.get("name") or ""
        if name and "arguments" in obj:
            return [{"name": name, "arguments": json.dumps(obj["arguments"])}]
        for key in list(obj.keys()):
            if key in self.KNOWN_TOOLS and isinstance(obj[key], dict):
                return [{"name": key, "arguments": json.dumps(obj[key])}]
        return None

    def _parse_text_tool_calls(self, content):
        if not content:
            return None
        raw = content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', "", raw).strip()
        
        all_calls = []
        
        # Try parsing entire content as single JSON first (handles multi-line)
        full_parsed = self._try_parse_json(raw)
        if full_parsed is not None:
            results = self._extract_tool_calls(full_parsed)
            if results:
                all_calls.extend(results)
                return self._build_tool_call_list(all_calls)
        
        # Fallback: try line-by-line (handles sequential JSON objects)
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            parsed = self._try_parse_json(line)
            if parsed is None:
                continue
            results = self._extract_tool_calls(parsed)
            if results:
                all_calls.extend(results)
        
        if all_calls:
            return self._build_tool_call_list(all_calls)
        return None

    def _try_parse_json(self, text):
        # First try as-is
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Strip to last complete } if truncated
        while "}" in text:
            end = text.rindex("}")
            candidate = text[:end+1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                text = text[:end]
        return None

    def _extract_tool_calls(self, parsed):
        if isinstance(parsed, list):
            results = []
            for item in parsed:
                r = self._parse_one_tool_obj(item)
                if r:
                    results.extend(r)
            return results
        elif isinstance(parsed, dict):
            return self._parse_one_tool_obj(parsed)
        return None

    def _build_tool_call_list(self, calls):
        return [
            {
                "id": f"call_{c['name']}_{i}",
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for i, c in enumerate(calls)
        ]

    def run(self, messages, tools, max_tokens=None):
        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": 0.01,
        }

        if tools and self.text_tools:
            payload["messages"] = self._build_text_tool_system_message(messages, tools)
        elif tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.endpoint, json=payload)
            if resp.status_code == 400:
                import sys; print(f"  [DEBUG] 400 error. Messages count: {len(payload['messages'])}, last role: {payload['messages'][-1]['role']}", file=sys.stderr, flush=True)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        message["finish_reason"] = choice.get("finish_reason", "stop")

        if self.text_tools and message.get("content"):
            tcs = self._parse_text_tool_calls(message["content"])
            if tcs:
                message["tool_calls"] = tcs
                message["finish_reason"] = "tool_calls"

        return message


class MockBackend(ModelBackend):
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_index = 0
        self.call_history = []

    def add_response(self, response):
        self.responses.append(response)

    def run(self, messages, tools, max_tokens=None):
        self.call_history.append({"messages": list(messages), "tools": tools, "max_tokens": max_tokens})
        if self.call_index < len(self.responses):
            resp = self.responses[self.call_index]
            self.call_index += 1
            return resp
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_finish_default",
                    "type": "function",
                    "function": {
                        "name": "finish",
                        "arguments": '{"summary": "done"}',
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }

    def reset(self):
        self.call_index = 0
        self.call_history = []
