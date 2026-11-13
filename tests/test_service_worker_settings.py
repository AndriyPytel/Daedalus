"""
The worker puts server secrets and a Run's resolved settings into `sdk` before the engine builds its model client, because a headless process has no panel to pick the model or store the keys.

Keys and base URLs come from the worker's environment, never from a `send` message; the settings of a `send` come from the gateway.
"""
import types

import pytest

import sdk
from service import worker

KEYS = '{"Anthropic": "sk-test-1", "OpenAI": "sk-test-2"}'
URLS = '{"Ollama": "http://ollama:11434"}'
SETTINGS = {
    "provider": "Anthropic", "model": "claude-a", "effort": "high", "interactive": False, "confirm": False, "budget_usd": 5.0,
}


@pytest.fixture
def restored():
    before = (sdk.get_api_keys(), sdk.get_base_urls(), sdk.get_last_model(), sdk.get_effort())
    yield
    sdk.set_api_keys(before[0])
    sdk.set_base_urls(before[1])
    sdk.set_last_model(*before[2])
    sdk.set_effort(before[3])


def test_keys_and_base_urls_in_the_environment_reach_the_sdk_settings(restored):
    worker.configure({"DAEDALUS_API_KEYS": KEYS, "DAEDALUS_BASE_URLS": URLS})
    assert sdk.get_api_key_for_provider("Anthropic") == "sk-test-1"
    assert sdk.get_api_key_for_provider("OpenAI") == "sk-test-2"
    assert sdk.get_base_url_for_provider("Ollama") == "http://ollama:11434"


def test_a_worker_with_no_secrets_in_its_environment_configures_nothing(restored):
    sdk.set_api_keys([])
    worker.configure({})
    assert sdk.get_api_keys() == []


def test_the_model_and_effort_of_a_send_reach_the_sdk(restored):
    worker.apply_settings(SETTINGS)
    assert sdk.get_last_model() == ("Anthropic", "claude-a")
    assert sdk.get_effort() == "high"


def test_a_setting_the_send_does_not_name_is_left_as_it_was(restored):
    sdk.set_effort("low")
    worker.apply_settings({"budget_usd": 1.0})
    assert sdk.get_effort() == "low"


def test_a_run_started_with_provider_model_effort_keys_and_addresses_builds_with_that_model(restored):
    sdk.set_api_keys([])
    sdk.set_last_model("", "")
    with pytest.raises(ValueError, match="No model selected"):
        sdk.Client(kernel=sdk.NO_KERNEL)._the_llm()
    worker.configure({"DAEDALUS_API_KEYS": KEYS, "DAEDALUS_BASE_URLS": URLS})
    worker.apply_settings(SETTINGS)
    built = sdk.Client(kernel=sdk.NO_KERNEL)._the_llm()
    assert built.model == "anthropic/claude-a" and built.api_key == "sk-test-1" and built.effort == "high"


def test_a_send_that_names_a_different_model_builds_a_different_client(restored):
    worker.configure({"DAEDALUS_API_KEYS": KEYS})
    client = sdk.Client(kernel=sdk.NO_KERNEL)
    worker.apply_settings({**SETTINGS, "provider": "OpenAI", "model": "gpt-b", "effort": "low"})
    assert client._the_llm().model == "openai/gpt-b"
    worker.apply_settings(SETTINGS)
    assert client._the_llm().model == "anthropic/claude-a"


def test_the_budget_is_exhausted_by_the_cost_events_that_reach_it():
    budget = worker.Budget(1.0)
    budget.watch(types.SimpleNamespace(kind="cost", data={"usd": 0.6}))
    budget.watch(types.SimpleNamespace(kind="step", data={"usd": 9.0}))
    assert budget.exhausted() is False
    budget.watch(types.SimpleNamespace(kind="cost", data={"usd": 0.5}))
    assert budget.exhausted() is True


def test_no_budget_is_never_exhausted():
    budget = worker.Budget(None)
    budget.watch(types.SimpleNamespace(kind="cost", data={"usd": 99.0}))
    assert budget.exhausted() is False


def test_the_run_stops_when_it_is_cancelled_or_when_its_budget_is_spent():
    budget = worker.Budget(1.0)
    cancelled = types.SimpleNamespace(is_set=lambda: False)
    stop = worker.stop_condition(cancelled, budget)
    assert stop() is False
    budget.watch(types.SimpleNamespace(kind="cost", data={"usd": 2.0}))
    assert stop() is True
    assert worker.stop_condition(types.SimpleNamespace(is_set=lambda: True), worker.Budget(None))() is True


def test_the_claude_subscription_provider_needs_no_key_in_a_worker(restored):
    sdk.set_api_keys([])
    worker.apply_settings({"provider": "Claude (subscription)", "model": "haiku"})
    assert sdk.Client(kernel=sdk.NO_KERNEL)._the_llm().model == "haiku"
