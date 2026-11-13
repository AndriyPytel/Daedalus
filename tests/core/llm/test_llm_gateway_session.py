"""
Level 1 -- the gateway session header `client_for` builds a client with.

OpenCode Go refuses every call that arrives without `x-opencode-session`, so
the header is part of being configured for that provider, not an option.
"""
import pytest

from core import model_catalog, settings
from core.llm import factory


@pytest.fixture(autouse=True)
def the_providers_are_keyed(monkeypatch):
    for provider in ("OpenCode Go", "OpenAI"):
        settings.instance().set_api_key_for_provider(provider, "sk-key")
    monkeypatch.setattr(model_catalog, "fetch", lambda *args, **kwargs: [])


def test_a_gateway_client_carries_the_session_header_the_gateway_routes_on():
    client = factory.client_for("OpenCode Go", "a-model", "medium")

    assert client.extra_headers["x-opencode-session"]


def test_one_process_is_one_gateway_session():
    first = factory.client_for("OpenCode Go", "a-model", "medium")
    second = factory.client_for("OpenCode Go", "a-model", "medium")

    assert first.extra_headers == second.extra_headers


def test_a_provider_that_routes_on_nothing_of_its_own_is_sent_no_header():
    assert factory.client_for("OpenAI", "a-model", "medium").extra_headers is None
