class ModelBackend:
    def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
        raise NotImplementedError


class LlamaCppBackend(ModelBackend):
    def __init__(
        self,
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="smollm3-3b",
        timeout=120,
        max_tokens=32768,
        seed=None,
        role=None,
    ):
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.seed = seed
        self.role = role

    def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
        import httpx

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repetition_penalty": 1.0,
            "chat_template_kwargs": {"enable_thinking": True},
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if grammar is not None:
            payload["grammar"] = grammar

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        with httpx.Client(timeout=timeout if timeout is not None else self.timeout) as client:
            headers = {"X-BID-Role": self.role} if self.role else {}
            response = client.post(self.endpoint, json=payload,
                                   **({"headers": headers} if headers else {}))
            if response.status_code == 400:
                import sys
                body = response.text[:2000]
                print(f"  [LLAMACPP 400] {body}", file=sys.stderr, flush=True)
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]
        message = choice["message"]
        message["finish_reason"] = choice.get("finish_reason", "stop")
        if "usage" in data:
            message["usage"] = data["usage"]

        return message


class MockBackend(ModelBackend):
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_index = 0
        self.call_history = []

    def add_response(self, response):
        self.responses.append(response)

    def run(self, messages, tools, max_tokens=None, timeout=None, grammar=None):
        call = {
            "messages": [dict(message) for message in messages],
            "tools": tools,
            "max_tokens": max_tokens,
        }
        if grammar is not None:
            call["grammar"] = grammar
        self.call_history.append(call)
        if self.call_index < len(self.responses):
            response = self.responses[self.call_index]
            self.call_index += 1
            return response
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_fallback",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "nonexistent"}'},
            }],
            "finish_reason": "tool_calls",
        }

    def reset(self):
        self.call_index = 0
        self.call_history = []
