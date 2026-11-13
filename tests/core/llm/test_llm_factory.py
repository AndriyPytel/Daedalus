"""
Level 1 -- the client for a run, built from the stored model, Effort, keys and addresses and from nothing else.
"""
import pytest

from core import model_catalog, settings
from core.llm import ClaudeCodeClient, factory


@pytest.fixture(autouse=True)
def no_provider_is_dialled(monkeypatch):
    monkeypatch.setattr(model_catalog, "fetch", lambda *args, **kwargs: [])


def test_a_gateway_model_builds_that_gateways_client():
    settings.instance().set_api_keys([{"provider": "OpenCode Go", "api_key": "sk-zen"}])

    client = factory.client_for("OpenCode Go", "glm-5.3-flash", "medium")

    assert client.model == "openai/glm-5.3-flash"
    assert client.api_base == "https://opencode.ai/zen/go/v1"
    assert client.extra_headers["x-opencode-session"]


def test_a_model_at_an_address_is_capped_at_the_output_ceiling_its_provider_lists(monkeypatch):
    settings.instance().set_base_urls({"Groq": "https://api.groq.com/openai/v1"})
    monkeypatch.setattr(model_catalog, "fetch", lambda *args, **kwargs: [
        {"id": "qwen/qwen3.8-27b", "display": "Qwen", "max_output": 16384}])

    client = factory.client_for("Groq", "qwen/qwen3.8-27b", "medium")

    assert client.output_ceiling == 16384


def test_a_provider_that_cannot_be_listed_is_built_with_no_ceiling(monkeypatch):
    settings.instance().set_base_urls({"Groq": "https://api.groq.com/openai/v1"})

    def unreachable(*args, **kwargs):
        raise model_catalog.httpx.ConnectError("down")

    monkeypatch.setattr(model_catalog, "fetch", unreachable)

    assert factory.client_for("Groq", "qwen/qwen3.8-27b", "medium").output_ceiling is None


def test_the_subscription_is_answered_by_its_own_client():
    assert isinstance(factory.client_for("Claude (subscription)", "opus", "medium"), ClaudeCodeClient)


def test_a_provider_with_no_key_or_address_is_refused_by_name():
    with pytest.raises(ValueError, match="OpenAI"):
        factory.client_for("OpenAI", "gpt-x", "medium")


def test_the_run_is_built_from_the_last_model_and_the_effort():
    settings.instance().set_api_key_for_provider("OpenAI", "sk-1")
    settings.instance().set_last_model("OpenAI", "gpt-x")
    settings.instance().set_effort("low")

    client = factory.client_from_settings()

    assert (client.model, client.api_key, client.effort) == ("openai/gpt-x", "sk-1", "low")


def test_no_model_picked_is_refused():
    settings.instance().set_last_model("", "")

    with pytest.raises(ValueError, match="No model selected"):
        factory.client_from_settings()


def test_a_model_picked_at_one_provider_is_not_resolved_through_another():
    settings.instance().set_api_key_for_provider("OpenAI", "sk-1")
    settings.instance().set_last_model("Anthropic", "gpt-x")

    with pytest.raises(ValueError, match="Anthropic"):
        factory.client_from_settings()
