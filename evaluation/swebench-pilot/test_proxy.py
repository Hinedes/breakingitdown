import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import proxy


class Upstream(BaseHTTPRequestHandler):
    calls = []
    completions = 0
    token_count = 10
    fail_usage = False
    fail_template = False

    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        Upstream.calls.append((self.path, payload))
        if self.path == "/apply-template":
            if Upstream.fail_template:
                self._json({"error": "no template"}, 500)
            else:
                self._json({"prompt": "rendered prompt"})
        elif self.path == "/tokenize":
            self._json({"tokens": list(range(Upstream.token_count))})
        elif self.path == "/v1/chat/completions":
            Upstream.completions += 1
            body = {"choices": [{"message": {"content": "ok"}}]}
            if not Upstream.fail_usage:
                body["usage"] = {"prompt_tokens": Upstream.token_count,
                                 "completion_tokens": 2,
                                 "prompt_tokens_details": {"cached_tokens": 4}}
            self._json(body)
        else:
            self._json({"error": "not found"}, 404)


def _start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _post(url, payload, path="/v1/chat/completions"):
    request = urllib.request.Request(
        url + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-BID-Role": "worker"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _fixture(tmp_path, tokens=100, seconds=60, context=50, model="m", seed=7):
    Upstream.calls = []
    Upstream.completions = 0
    Upstream.token_count = 10
    Upstream.fail_usage = False
    Upstream.fail_template = False
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    _start(upstream)
    log = tmp_path / "proxy.jsonl"
    deadline = tmp_path / "deadline.json"
    trace = tmp_path / "trace.jsonl"
    server = proxy.build_server(
        "127.0.0.1:0",
        f"http://127.0.0.1:{upstream.server_port}/v1/chat/completions",
        seconds, tokens, context, model, seed, log, "cell-1", "bid",
        deadline_file=deadline, trace_path=trace,
    )
    _start(server)
    return upstream, server, f"http://127.0.0.1:{server.server_port}", log


def _log_lines(log, count):
    for _ in range(100):
        if log.exists() and len(log.read_text(encoding="utf-8").splitlines()) >= count:
            break
        time.sleep(0.02)
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_proxy_preflights_clamps_and_logs_actual_usage(tmp_path):
    upstream, server, url, log = _fixture(tmp_path)
    status, body = _post(url, {"model": "m", "max_tokens": 4096, "messages": [{"role": "user", "content": "x"}]})
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "ok"
    completion_payload = [p for path, p in Upstream.calls if path == "/v1/chat/completions"][0]
    assert completion_payload["max_tokens"] == 40  # min(4096, 100-10, 50-10)
    assert completion_payload["temperature"] == 0.6
    assert completion_payload["seed"] == 7
    assert completion_payload["chat_template_kwargs"]["enable_thinking"] is True
    lines = _log_lines(log, 1)
    assert lines[-1]["status"] == 200
    assert lines[-1]["role"] == "worker"
    assert lines[-1]["prompt_tokens"] == 10 and lines[-1]["cached_tokens"] == 4
    assert server.budget.deadline_file.exists()
    deadline = json.loads(server.budget.deadline_file.read_text(encoding="utf-8"))
    assert deadline["deadline_monotonic"] > deadline["started_monotonic"]
    trace = [json.loads(line) for line in server.budget.trace_path.read_text(encoding="utf-8").splitlines()]
    assert trace[-1]["request"]["model"] == "m"
    assert trace[-1]["response"]["choices"][0]["message"]["content"] == "ok"
    server.shutdown()
    upstream.shutdown()


def test_proxy_fails_closed_on_missing_usage_and_stays_closed(tmp_path):
    upstream, server, url, log = _fixture(tmp_path)
    Upstream.fail_usage = True
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 502
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 429
    assert Upstream.completions == 1
    lines = _log_lines(log, 2)
    assert [line["status"] for line in lines] == [502, 429]
    server.shutdown()
    upstream.shutdown()


def test_proxy_rejects_before_upstream_when_budget_or_wall_is_gone(tmp_path):
    upstream, server, url, log = _fixture(tmp_path, tokens=10)
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 429
    assert Upstream.completions == 0
    server.shutdown()
    upstream.shutdown()

    upstream, server, url, log = _fixture(tmp_path, seconds=-1)
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 429
    assert Upstream.completions == 0
    server.shutdown()
    upstream.shutdown()


def test_proxy_logs_actual_usage_when_reservation_is_overrun(tmp_path):
    upstream, server, url, log = _fixture(tmp_path)
    status, _ = _post(url, {"model": "m", "max_tokens": 1, "messages": []})
    assert status == 502
    lines = _log_lines(log, 1)
    assert lines[-1]["status"] == 502
    assert lines[-1]["last_usage"]["actual_tokens"] == 12
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 429
    server.shutdown()
    upstream.shutdown()


def test_proxy_rejects_wrong_model_stream_and_preflight_failure(tmp_path):
    upstream, server, url, log = _fixture(tmp_path)
    assert _post(url, {"model": "other", "max_tokens": 2, "messages": []})[0] == 400
    assert _post(url, {"model": "m", "max_tokens": 2, "messages": [], "stream": True})[0] == 400
    Upstream.fail_template = True
    status, _ = _post(url, {"model": "m", "max_tokens": 2, "messages": []})
    assert status == 502
    assert Upstream.completions == 0
    lines = _log_lines(log, 1)
    assert lines[-1]["last_usage"] == {}
    assert lines[-1]["usage_known"] is False
    server.shutdown()
    upstream.shutdown()
