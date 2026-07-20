import json
import re


class ModelBackend:
    def run(self, messages, tools, max_tokens=None):
        raise NotImplementedError


class LlamaCppBackend(ModelBackend):
    def __init__(
        self,
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="smollm3-3b",
        timeout=120,
        text_tools=False,
        max_tokens=8192,
    ):
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.text_tools = text_tools
        self.max_tokens = max_tokens

    def _build_text_tool_system_message(self, messages, tools):
        if not tools:
            return messages

        signatures = []
        for tool in tools:
            function = tool["function"]
            properties = function.get("parameters", {}).get("properties", {})
            required = set(function.get("parameters", {}).get("required", []))
            parameters = []
            for name, schema in properties.items():
                suffix = "" if name in required else "?"
                parameters.append(f"{name}{suffix}:{schema.get('type', 'str')}")
            signatures.append(f"{function['name']}({','.join(parameters)})")

        extra = (
            "Tools: " + "; ".join(signatures) + "\n"
            'Call tools as JSON: {"tool_calls":[{"tool":"read_file","arguments":{"path":"docs/x.md"}}]}\n'
            "Use only listed tools. When all work is complete, output exactly Done."
        )

        output = []
        inserted = False
        for message in messages:
            if message["role"] == "system" and not inserted:
                output.append({"role": "system", "content": message["content"] + "\n" + extra})
                inserted = True
            else:
                output.append(message)
        if not inserted:
            output.insert(0, {"role": "system", "content": extra})
        return output

    KNOWN_TOOLS = {
        "list_files",
        "read_file",
        "write_file",
        "replace_text",
        "create_file",
        "new_file",
        "save_file",
    }

    def _parse_one_tool_obj(self, obj):
        if not isinstance(obj, dict):
            return None
        calls = obj.get("tool_calls") or obj.get("toolCalls")
        if isinstance(calls, list):
            results = []
            for call in calls:
                name = call.get("tool") or call.get("name") or ""
                arguments = call.get("arguments") or call.get("parameters") or {}
                if name:
                    results.append({"name": name, "arguments": json.dumps(arguments)})
            return results
        name = obj.get("tool") or obj.get("name") or ""
        if name and "arguments" in obj:
            return [{"name": name, "arguments": json.dumps(obj["arguments"])}]
        for key, value in obj.items():
            if key in self.KNOWN_TOOLS and isinstance(value, dict):
                return [{"name": key, "arguments": json.dumps(value)}]
        return None

    def _parse_text_tool_calls(self, content):
        if not content:
            return None
        raw = content.strip()
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', "", raw).strip()

        parsed = self._try_parse_json(raw)
        if parsed is not None:
            calls = self._extract_tool_calls(parsed)
            if calls:
                return self._build_tool_call_list(calls)

        calls = []
        for line in raw.split("\n"):
            parsed = self._try_parse_json(line.strip())
            if parsed is None:
                continue
            extracted = self._extract_tool_calls(parsed)
            if extracted:
                calls.extend(extracted)
        return self._build_tool_call_list(calls) if calls else None

    def _try_parse_json(self, text):
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        while "}" in text:
            end = text.rindex("}")
            candidate = text[: end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                text = text[:end]
        return None

    def _extract_tool_calls(self, parsed):
        if isinstance(parsed, list):
            results = []
            for item in parsed:
                calls = self._parse_one_tool_obj(item)
                if calls:
                    results.extend(calls)
            return results
        if isinstance(parsed, dict):
            return self._parse_one_tool_obj(parsed)
        return None

    def _build_tool_call_list(self, calls):
        return [
            {
                "id": f"call_{call['name']}_{index}",
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for index, call in enumerate(calls)
        ]

    def run(self, messages, tools, max_tokens=None):
        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": 0.01,
            "chat_template_kwargs": {"enable_thinking": False},
        }

        if tools and self.text_tools:
            payload["messages"] = self._build_text_tool_system_message(messages, tools)
        elif tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        message = choice["message"]
        message["finish_reason"] = choice.get("finish_reason", "stop")

        if self.text_tools and message.get("content"):
            tool_calls = self._parse_text_tool_calls(message["content"])
            if tool_calls:
                message["tool_calls"] = tool_calls
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
        self.call_history.append({
            "messages": [dict(message) for message in messages],
            "tools": tools,
            "max_tokens": max_tokens,
        })
        if self.call_index < len(self.responses):
            response = self.responses[self.call_index]
            self.call_index += 1
            return response
        return {
            "role": "assistant",
            "content": "Done",
            "tool_calls": None,
            "finish_reason": "stop",
        }

    def reset(self):
        self.call_index = 0
        self.call_history = []
