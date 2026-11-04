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

import httpx
import pytest

from core.model_catalog import (
    CLAUDE, api_base_for, fetch, needs_base_url, needs_no_key, providers,
)
from tests import modelmania_harness

RECORDED = modelmania_harness.RECORDED_DIR


def _recorded(name):
    with open(os.path.join(RECORDED, name), encoding="utf-8") as handle:
        return json.load(handle)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.last_path = self.path
        self.server.last_headers = {name.lower(): value for name, value in self.headers.items()}
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
    """
    A real HTTP server on loopback, for one test.

    `serve_forever`'s default poll interval is 0.5s, and `shutdown()` only
    sets a flag that loop checks on its next wake — so tearing this down at
    the default rate cost every test here half a second it never used. A
    10ms poll interval keeps that overhead off tests whose subject is a
    request against the model catalog, not the shutdown of a local server.
    """
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.payload = {}
    server.status = 200
    server.last_path = None
    server.last_headers = {}
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    try:
        yield server, "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_openai_models_come_back_as_ids(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("openai_models.json")

    models = fetch("OpenAI", "sk-test", base_url=base,
                   accepts_images=_sees({"openai/gpt-4o": True, "openai/gpt-4o-mini": True}))

    assert models[:2] == [
        {"id": "gpt-4o", "display": "gpt-4o"},
        {"id": "gpt-4o-mini", "display": "gpt-4o-mini"},
    ]


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

    models = fetch("Home server", "", base_url=base + "/v1")

    assert models == [
        {"id": "llama3.2:latest", "display": "llama3.2:latest"},
        {"id": "qwen2.5-coder:7b", "display": "qwen2.5-coder:7b"},
    ]
    assert server.last_path.startswith("/v1/models")


def test_a_bare_local_host_is_asked_at_its_v1_root(provider_endpoint):
    """
    What a user types is a server address — `http://localhost:11434`. Ollama,
    llama.cpp and LM Studio all serve the OpenAI shape under `/v1`, so that is
    the addon's business to append rather than the user's to remember.
    """
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    fetch("Home server", "", base_url=base)

    assert server.last_path.startswith("/v1/models")


def test_a_local_base_url_that_already_says_v1_is_not_doubled(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    fetch("Home server", "", base_url=base + "/v1/")

    assert server.last_path.startswith("/v1/models")


def test_a_local_token_is_sent_when_there_is_one(provider_endpoint):
    """A local server may still want a token; one that does not ignores it."""
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    fetch("Home server", "sk-local", base_url=base)

    assert server.last_headers.get("authorization") == "Bearer sk-local"


def test_a_local_endpoint_with_no_token_sends_no_authorisation(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    fetch("Home server", "", base_url=base)

    assert "authorization" not in server.last_headers


def test_a_local_provider_with_no_base_url_refuses_rather_than_guessing():
    """
    The endpoint used to be read out of the API-key field, so an address typed
    there became the base and a key typed there became a broken one. There is
    nowhere left to guess from.
    """
    with pytest.raises(ValueError) as raised:
        fetch("Home server", "http://127.0.0.1:1/v1")

    assert "Base URL" in str(raised.value)


def test_a_rejected_key_raises_rather_than_returning_a_partial_list(provider_endpoint):
    server, base = provider_endpoint
    server.status = 401
    server.payload = {"error": {"message": "Incorrect API key provided"}}

    with pytest.raises(Exception):
        fetch("OpenAI", "sk-wrong", base_url=base)


def test_an_unreachable_host_raises():
    with pytest.raises(Exception):
        fetch("OpenAI", "sk-test", base_url="http://127.0.0.1:1")


def test_the_subscription_is_configured_by_neither_a_key_nor_an_address():
    """
    The first provider with neither. The `claude` CLI holds the credential —
    the subscription's own OAuth, which `apiKeySource: none` in the recorded
    transcript is the proof of — so a page asking for a key would be asking for
    something nothing reads.
    """
    assert needs_no_key(CLAUDE) is True
    assert needs_base_url(CLAUDE) is False
    assert not any(needs_no_key(provider) for provider in providers()
                   if provider != CLAUDE)


def test_the_subscription_offers_every_anthropic_version(provider_endpoint):
    """
    An alias hides which version answers, so each Anthropic id models.dev
    lists is offered by version, and no other provider's.
    """
    server, base = provider_endpoint
    server.payload = _recorded("models_dev_api.json")

    models = fetch(CLAUDE, "", base_url=base)

    assert server.last_path == "/api.json"
    assert models == [
        {"id": "claude-sonnet-5", "display": "Claude Sonnet 5"},
        {"id": "claude-sonnet-5-5", "display": "Claude Sonnet 5.5"},
        {"id": "claude-opus-5-5", "display": "Claude Opus 5.5"},
    ]


def test_the_subscription_says_so_when_models_dev_is_down(provider_endpoint):
    """Like every other provider's endpoint, an outage is a failure, not an empty list."""
    server, base = provider_endpoint
    server.status = 503

    with pytest.raises(httpx.HTTPStatusError):
        fetch(CLAUDE, "", base_url=base)


def test_a_vendor_litellm_names_itself_is_given_no_address():
    """Passing an api_base for OpenAI, Anthropic or Google overrides what LiteLLM knows."""
    assert api_base_for("OpenAI") is None
    assert api_base_for("Anthropic") is None
    assert api_base_for("Google") is None


def test_a_local_server_is_still_reached_where_the_user_said():
    assert api_base_for("Home server", "http://localhost:11434") == "http://localhost:11434/v1"
    assert api_base_for("Home server", "") is None


def test_opencode_go_lists_what_the_gateway_sells(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("opencode_go_models.json")

    models = fetch("OpenCode Go", "sk-zen", base_url=base + "/v1")

    assert models[:2] == [
        {"id": "minimax-m3", "display": "minimax-m3"},
        {"id": "minimax-m2.7", "display": "minimax-m2.7"},
    ]
    assert {m["id"] for m in models} >= {"kimi-k3", "qwen3.8-max", "grok-4.5"}


def test_openrouter_is_litellms_own_route_and_needs_no_address():
    """LiteLLM carries the endpoint, the per-model output ceiling and the price."""
    assert api_base_for("OpenRouter") is None


def test_openrouter_lists_what_it_sells_under_its_own_ids(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("openrouter_models.json")

    models = fetch("OpenRouter", "sk-or-test", base_url=base + "/v1")

    assert models[0] == {"id": "anthropic/claude-sonnet-4.5",
                         "display": "anthropic/claude-sonnet-4.5"}
    assert server.last_headers.get("authorization") == "Bearer sk-or-test"


def _sees(verdicts):
    """A stand-in for `llm.accepts_images`: True, False, or None for unheard-of."""
    return lambda model: verdicts.get(model)


def test_a_vendor_offers_only_models_that_can_read_a_drawing(provider_endpoint):
    """
    The addon's work is a sheet in and code out, so a model that cannot see one
    is no use for it — that is narrower than dropping embeddings: `o3-mini` is
    a chat model with no eyes, and picking it means the drawing never arrives.
    """
    server, base = provider_endpoint
    server.payload = _recorded("openai_models.json")

    models = fetch("OpenAI", "sk-test", base_url=base, accepts_images=_sees({
        "openai/gpt-4o": True,
        "openai/gpt-4o-mini": True,
        "openai/o3-mini": False,
        "openai/text-embedding-3-small": False,
    }))

    assert models == [
        {"id": "gpt-4o", "display": "gpt-4o"},
        {"id": "gpt-4o-mini", "display": "gpt-4o-mini"},
    ]


def test_a_model_litellm_has_never_heard_of_is_still_offered(provider_endpoint):
    """
    The table ships with LiteLLM, so it is always a release behind the model
    everyone wants. An id it does not know is a new model, not a bad one.
    """
    server, base = provider_endpoint
    server.payload = {"data": [{"id": "gpt-5.6-sol"}]}

    models = fetch("OpenAI", "sk-test", base_url=base, accepts_images=_sees({}))

    assert models == [{"id": "gpt-5.6-sol", "display": "gpt-5.6-sol"}]


def test_an_unheard_of_model_whose_name_says_what_it_is_is_still_dropped(provider_endpoint):
    server, base = provider_endpoint
    server.payload = {"data": [
        {"id": "text-embedding-4"},
        {"id": "tts-2-hd"},
        {"id": "gpt-image-2"},
        {"id": "sora-3"},
        {"id": "gpt-5.6-sol"},
    ]}

    models = fetch("OpenAI", "sk-test", base_url=base, accepts_images=_sees({}))

    assert [m["id"] for m in models] == ["gpt-5.6-sol"]


def test_google_is_filtered_by_sight_on_top_of_generate_content(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("google_models.json")

    models = fetch("Google", "AIza-test", base_url=base, accepts_images=_sees({
        "gemini/gemini-2.5-pro": True,
        "gemini/gemini-2.5-flash": False,
    }))

    assert models == [{"id": "gemini-2.5-pro", "display": "Gemini 2.5 Pro"}]


def test_a_gateway_lists_what_it_sells_and_is_asked_nothing(provider_endpoint):
    """
    LiteLLM's table names three vendors. OpenCode Go sells under ids of its own
    and a local server serves whatever was pulled, so a verdict on those is not
    a filter but a blank list.
    """
    server, base = provider_endpoint
    server.payload = _recorded("opencode_go_models.json")

    def refuse_everything(model):
        raise AssertionError("the gateway's own list is not LiteLLM's to judge: %s" % model)

    models = fetch("OpenCode Go", "sk-zen", base_url=base + "/v1",
                   accepts_images=refuse_everything)

    assert len(models) == len(server.payload["data"])


def test_a_local_server_lists_what_it_serves_and_is_asked_nothing(provider_endpoint):
    server, base = provider_endpoint
    server.payload = _recorded("local_models.json")

    def refuse_everything(model):
        raise AssertionError("a pulled model is not LiteLLM's to judge: %s" % model)

    models = fetch("Home server", "", base_url=base, accepts_images=refuse_everything)

    assert len(models) == 2
