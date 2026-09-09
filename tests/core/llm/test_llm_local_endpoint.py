"""
Level 1 — a local OpenAI-compatible endpoint really works as a provider.

Stands up a stdlib HTTP server that speaks the OpenAI streaming protocol and
drives LiteLLM at it over a real socket. Nothing about LiteLLM is stubbed here;
this is the same path a user's Ollama or LM Studio would take.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core.llm import LiteLLMClient
from conftest import collect

DELTAS = ["A ", "10mm ", "box."]


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.last_body = json.loads(
            self.rfile.read(int(self.headers["Content-Length"])).decode()
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in DELTAS:
            self._event({"choices": [{"index": 0, "delta": {"content": text},
                                      "finish_reason": None}]})
        self._event({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _event(self, payload):
        payload = dict(payload, id="chatcmpl-local", object="chat.completion.chunk",
                       created=1730000000, model="local-model")
        self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


@pytest.fixture
def local_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.last_body = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:%d/v1" % server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_local_openai_compatible_endpoint_streams(local_endpoint):
    server, base = local_endpoint
    client = LiteLLMClient(model="openai/local-model", api_key="not-needed", api_base=base)

    reply = collect(client.stream([{"role": "user", "content": "Describe it."}]))

    assert reply.text == "A 10mm box."
    assert server.last_body["stream"] is True
    assert server.last_body["messages"] == [{"role": "user", "content": "Describe it."}]


def test_how_hard_to_think_reaches_a_model_litellm_has_no_entry_for(local_endpoint):
    """
    The gateway model kimi-k3 runs on is one LiteLLM has never heard of, and it
    drops unknown params rather than sending them. What the server received is
    the only proof the effort was not dropped on the way out (FCC-108).
    """
    server, base = local_endpoint
    client = LiteLLMClient(model="openai/local-model", api_key="not-needed", api_base=base)

    collect(client.stream([{"role": "user", "content": "Describe it."}]))

    assert server.last_body["reasoning_effort"] == "medium"
    assert "allowed_openai_params" not in server.last_body, "our plumbing leaked into the request"
