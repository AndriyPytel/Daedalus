"""
Level 1 — the model list a key really entitles you to, over a real socket.

A stdlib HTTP server serves each provider's recorded /models payload and
``fetch`` is pointed at it; the assertions are on the returned list, never on
how the request was built. Nothing is mocked — the path is the one a user's
key takes.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core.model_catalog import fetch

RECORDED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded")


def _recorded(name):
    with open(os.path.join(RECORDED, name), encoding="utf-8") as handle:
        return json.load(handle)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.last_path = self.path
        body = json.dumps(self.server.payload).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def provider_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.payload = {}
    server.status = 200
    server.last_path = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_models_come_back_as_ids(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("openai_models.json")

    models = fetch("OpenAI", "sk-test", base_url=base)

    assert models[:2] == [
        {"id": "gpt-4o", "display": "gpt-4o"},
        {"id": "gpt-4o-mini", "display": "gpt-4o-mini"},
    ]
    assert {m["id"] for m in models} >= {"o3-mini"}


def test_anthropic_models_use_the_providers_display_name(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("anthropic_models.json")

    models = fetch("Anthropic", "sk-ant-test", base_url=base)

    assert models[0] == {"id": "claude-sonnet-4-5-20250929", "display": "Claude Sonnet 4.5"}
    assert len(models) == 3


def test_google_strips_the_models_prefix_and_drops_non_chat_models(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("google_models.json")

    models = fetch("Google", "AIza-test", base_url=base)

    assert models == [
        {"id": "gemini-2.5-pro", "display": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "display": "Gemini 2.5 Flash"},
    ]


def test_a_local_endpoint_lists_what_has_been_pulled(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    models = fetch("Custom/Local", base + "/v1")

    assert models == [
        {"id": "llama3.2:latest", "display": "llama3.2:latest"},
        {"id": "qwen2.5-coder:7b", "display": "qwen2.5-coder:7b"},
    ]
    assert server.last_path.startswith("/v1/models")


def test_a_rejected_key_raises_rather_than_returning_a_partial_list(provider_endpoint):
    server, base = provider_endpoint
    server.status = 401
    server.payload = {"error": {"message": "Incorrect API key provided"}}

    with pytest.raises(Exception):
        fetch("OpenAI", "sk-wrong", base_url=base)


def test_an_unreachable_host_raises():
    with pytest.raises(Exception):
        fetch("OpenAI", "sk-test", base_url="http://127.0.0.1:1")
