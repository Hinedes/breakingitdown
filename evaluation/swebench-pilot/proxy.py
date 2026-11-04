"""Controller-side model proxy for one pilot cell.

The model server stays on host loopback. Arm containers get only this proxy:
one POST path, a tokenizer + chat-template preflight, a hard context/budget
reservation, fixed sampling and seed, and an append-only controller log.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import protocol

CHAT_PATH = "/v1/chat/completions"
MAX_BODY = 16 * 1024 * 1024
SAMPLING = {
    "temperature": 0.6,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "repetition_penalty": 1.0,
    "chat_template_kwargs": {"enable_thinking": True},
}


class BudgetExceeded(Exception):
    pass


class PreflightError(Exception):
    pass


def _post_json(url, payload, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except Exception as exc:
        raise PreflightError(f"{url}: {exc}") from exc


def count_prompt_tokens(upstream, payload, timeout):
    """Use the model server's own chat template and tokenizer, fail closed."""
    base = upstream.rsplit("/v1/", 1)[0]
    template_payload = {
        "messages": payload.get("messages") or [],
    }
    if payload.get("tools"):
        template_payload["tools"] = payload["tools"]
    if payload.get("chat_template_kwargs"):
        template_payload["chat_template_kwargs"] = payload["chat_template_kwargs"]
    rendered = _post_json(base + "/apply-template", template_payload, timeout)
    prompt = rendered.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise PreflightError("apply-template returned no prompt")
    tokenized = _post_json(
        base + "/tokenize",
        {"content": prompt, "add_special": True},
        timeout,
    )
    tokens = tokenized.get("tokens")
    if not isinstance(tokens, list):
        raise PreflightError("tokenize returned no token list")
    return len(tokens)


class ProxyBudget:
    def __init__(self, seconds, tokens, context, log_path, cell="", role="unknown",
                 deadline_file=None, trace_path=None):
        self.seconds = seconds
        self.tokens = tokens
        self.context = context
        self.deadline = None
        self.used = 0
        self.reserved = 0
        self.failed = None
        self.lock = threading.Lock()
        self.last_usage = {}
        self.log_path = Path(log_path) if log_path else None
        self.deadline_file = Path(deadline_file) if deadline_file else None
        self.trace_path = Path(trace_path) if trace_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.trace_path:
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self.cell = cell
        self.role = role

    def begin_request(self):
        with self.lock:
            self.last_usage = {}

    def _write_deadline_locked(self, started):
        if not self.deadline_file:
            return
        record = {
            "cell": self.cell,
            "role": self.role,
            "started_monotonic": started,
            "deadline_monotonic": self.deadline,
            "seconds": self.seconds,
            "ts": time.time(),
        }
        temp = self.deadline_file.with_suffix(".tmp")
        temp.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, self.deadline_file)

    def anchor(self):
        """Start the shared 600 s clock at the first model request, after setup."""
        with self.lock:
            if self.deadline is not None:
                return
            started = time.monotonic()
            self.deadline = started + self.seconds
            self._write_deadline_locked(started)

    def remaining_wall(self):
        if self.deadline is None:
            return self.seconds
        return self.deadline - time.monotonic()

    def reserve(self, prompt_tokens, requested_output):
        with self.lock:
            if self.failed:
                raise BudgetExceeded(self.failed)
            if self.deadline is None:
                started = time.monotonic()
                self.deadline = started + self.seconds
                self._write_deadline_locked(started)
            remaining_wall = self.remaining_wall()
            if remaining_wall <= 0:
                self.failed = "paired-condition wall budget exceeded"
                raise BudgetExceeded(self.failed)
            if prompt_tokens >= self.context:
                self.failed = "prompt exceeds the 65536-token context"
                raise BudgetExceeded(self.failed)
            left = self.tokens - self.used - self.reserved
            if left <= prompt_tokens:
                self.failed = "paired-condition token budget exhausted before preflight"
                raise BudgetExceeded(self.failed)
            output = min(
                int(requested_output or protocol.REQUEST_OUTPUT_LIMIT),
                protocol.REQUEST_OUTPUT_LIMIT,
                left - prompt_tokens,
                self.context - prompt_tokens,
            )
            if output <= 0:
                self.failed = "no output tokens remain after prompt"
                raise BudgetExceeded(self.failed)
            amount = prompt_tokens + output
            self.reserved += amount
            return output, amount, remaining_wall

    def release(self, amount):
        with self.lock:
            self.reserved = max(0, self.reserved - amount)

    def commit(self, amount, usage):
        if not isinstance(usage, dict):
            self._fail("missing usage")
            raise PreflightError("missing usage")
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if not isinstance(prompt, int) or not isinstance(completion, int) or prompt < 0 or completion < 0:
            self._fail("invalid usage")
            raise PreflightError("invalid usage")
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        if not isinstance(cached, int) or cached < 0 or cached > prompt:
            self._fail("invalid cached token count")
            raise PreflightError("invalid cached token count")
        actual = prompt + completion
        self.last_usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": cached,
            "actual_tokens": actual,
        }
        with self.lock:
            self.reserved = max(0, self.reserved - amount)
            if actual > amount or self.used + actual > self.tokens or prompt + completion > self.context:
                self.failed = "actual usage exceeded the reservation/context"
                raise PreflightError(self.failed)
            self.used += actual
        return prompt, completion, cached, actual

    def _fail(self, reason):
        with self.lock:
            self.failed = reason

    def log(self, record):
        if not self.log_path:
            return
        record = {
            "ts": time.time(),
            "cell": self.cell,
            "role": self.role,
            "used_tokens": self.used,
            "reserved_tokens": self.reserved,
            "failed": self.failed,
            **record,
        }
        with self.lock:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()

    def trace(self, record):
        """Bounded request/response trace for reproducibility; controller-only."""
        if not self.trace_path:
            return
        text = json.dumps(record, sort_keys=True)
        if len(text) > 200_000:
            record = {"truncated": True, "preview": text[:200_000]}
        with self.lock:
            with open(self.trace_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()


class Handler(BaseHTTPRequestHandler):
    upstream = ""
    budget = None
    model = None
    seed = None

    def log_message(self, fmt, *args):
        sys.stderr.write("proxy: " + (fmt % args) + "\n")

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        request_id = uuid.uuid4().hex[:12]
        role = self.headers.get("X-BID-Role") or self.budget.role
        started = time.monotonic()
        status = 502
        error = ""
        prompt_est = max_out = reserved = 0
        usage_record = {}
        payload = None
        upstream_body = None
        try:
            if self.path != CHAT_PATH:
                status, error = 404, "only /v1/chat/completions is proxied"
                self._send(404, json.dumps({"error": error}).encode())
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                status, error = 413, "invalid body size"
                self._send(413, json.dumps({"error": error}).encode())
                return
            payload = json.loads(self.rfile.read(length))
            if payload.get("stream"):
                status, error = 400, "streaming is not proxied"
                self._send(400, json.dumps({"error": error}).encode())
                return
            if int(payload.get("n", 1)) != 1:
                status, error = 400, "only one completion per request"
                self._send(400, json.dumps({"error": error}).encode())
                return
            if self.model and payload.get("model") != self.model:
                status, error = 400, "model does not match the frozen cell model"
                self._send(400, json.dumps({"error": error}).encode())
                return

            self.budget.begin_request()
            self.budget.anchor()
            payload.update(SAMPLING)
            if self.seed is not None:
                payload["seed"] = int(self.seed)
            requested = payload.get("max_tokens", payload.get("max_completion_tokens"))
            payload.pop("max_completion_tokens", None)
            remaining = self.budget.remaining_wall()
            if remaining <= 0:
                raise BudgetExceeded("paired-condition wall budget exceeded")
            prompt_est = count_prompt_tokens(self.upstream, payload, min(60, max(1, remaining)))
            max_out, reserved, remaining = self.budget.reserve(prompt_est, requested)
            payload["max_tokens"] = max_out
            status, error = 502, ""
            request = urllib.request.Request(
                self.upstream, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-BID-Role": role},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=min(300, max(1, remaining))) as response:
                    body = response.read()
                    status = response.status
            except urllib.error.HTTPError as exc:
                self.budget.release(reserved)
                status = exc.code
                body = exc.read() or b'{"error":"upstream error"}'
                error = f"upstream HTTP {status}"
                self._send(status, body)
                return
            except Exception as exc:
                self.budget.release(reserved)
                status, error = 502, f"upstream unreachable: {exc}"
                self._send(502, json.dumps({"error": error}).encode())
                return

            try:
                decoded = json.loads(body)
                upstream_body = decoded
                usage = decoded.get("usage")
                prompt, completion, cached, actual = self.budget.commit(reserved, usage)
                usage_record = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cached_tokens": cached,
                    "actual_tokens": actual,
                }
            except PreflightError as exc:
                status, error = 502, str(exc)
                self._send(502, json.dumps({"error": error}).encode())
                return
            self._send(status, body)
        except BudgetExceeded as exc:
            status, error = 429, str(exc)
            self._send(429, json.dumps({"error": error}).encode())
        except PreflightError as exc:
            status, error = 502, str(exc)
            self._send(502, json.dumps({"error": error}).encode())
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            status, error = 400, f"invalid request: {exc}"
            self._send(400, json.dumps({"error": error}).encode())
        finally:
            self.budget.log({
                "request_id": request_id,
                "role": role,
                "status": status,
                "error": error,
                "model": (payload or {}).get("model"),
                "seed": (payload or {}).get("seed"),
                "prompt_estimate": prompt_est,
                "max_tokens": max_out,
                "reserved": reserved,
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "last_usage": self.budget.last_usage,
                "usage_known": bool(self.budget.last_usage),
                **usage_record,
            })
            if payload is not None:
                self.budget.trace({
                    "request_id": request_id,
                    "role": role,
                    "status": status,
                    "error": error,
                    "request": payload,
                    "response": upstream_body,
                })


def build_server(listen, upstream, seconds, tokens, context=protocol.CONTEXT_TOKENS, model=None,
                 seed=None, log_path=None, cell="", role="unknown", deadline_file=None,
                 trace_path=None):
    state = ProxyBudget(seconds, tokens, context, log_path, cell, role,
                        deadline_file=deadline_file, trace_path=trace_path)
    handler = type("BoundHandler", (Handler,), {
        "upstream": upstream,
        "budget": state,
        "model": model,
        "seed": seed,
    })
    host, _, port = listen.rpartition(":")
    server = ThreadingHTTPServer((host or "127.0.0.1", int(port)), handler)
    server.budget = state
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:18080")
    parser.add_argument("--upstream", default="http://127.0.0.1:8081/v1/chat/completions")
    parser.add_argument("--seconds", type=int, default=protocol.WALL_BUDGET_SECONDS)
    parser.add_argument("--tokens", type=int, default=protocol.TOKEN_BUDGET)
    parser.add_argument("--context", type=int, default=protocol.CONTEXT_TOKENS)
    parser.add_argument("--model", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--cell", default="")
    parser.add_argument("--role", default="unknown")
    parser.add_argument("--log", default=None)
    parser.add_argument("--deadline-file", default=None)
    parser.add_argument("--trace", default=None)
    args = parser.parse_args()
    server = build_server(args.listen, args.upstream, args.seconds, args.tokens,
                          args.context, args.model, args.seed, args.log, args.cell,
                          args.role, args.deadline_file, args.trace)
    print(f"proxy {args.listen} -> {args.upstream} ({args.seconds}s/{args.tokens} tokens, ctx {args.context})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
