"""
Level 1 -- the gateway session header `client_from_settings` builds a client with.

OpenCode Go refuses every call that arrives without `x-opencode-session`, so
the header is part of being configured for that provider, not an option.
"""
import types

from core.llm import factory


def _settings(provider, base_url=""):
    return types.SimpleNamespace(
        get_llm_credentials=lambda model_id=None: (provider, "a-model", "sk-key"),
        get_base_url_for_provider=lambda name: base_url,
    )


def test_a_gateway_client_carries_the_session_header_the_gateway_routes_on(monkeypatch):
    monkeypatch.setattr(factory.settings, "instance", lambda: _settings("OpenCode Go"))

    client = factory.client_from_settings()

    assert client.extra_headers["x-opencode-session"]


def test_one_process_is_one_gateway_session(monkeypatch):
    monkeypatch.setattr(factory.settings, "instance", lambda: _settings("OpenCode Go"))

    first = factory.client_from_settings()
    second = factory.client_from_settings()

    assert first.extra_headers == second.extra_headers


def test_a_provider_that_routes_on_nothing_of_its_own_is_sent_no_header(monkeypatch):
    monkeypatch.setattr(factory.settings, "instance", lambda: _settings("OpenAI"))

    assert factory.client_from_settings().extra_headers is None
