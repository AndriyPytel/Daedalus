"""
The worker puts server secrets and a Run's resolved settings into `sdk` before the engine builds its model clients, because a headless process has no panel to fill the model registry or the stored keys.

Keys and base URLs come from the worker's environment, never from a `send` message; the settings of a `send` come from the gateway.
"""
import types

import pytest

import sdk
from service import worker

KEYS = '{"Anthropic": "sk-test-1", "OpenAI": "sk-test-2"}'
URLS = '{"Ollama": "http://ollama:11434"}'
SETTINGS = {
    "role_table": [{"role": "design", "provider": "Anthropic", "model": "claude-a", "effort": "high"}],
    "default_model": {"provider": "Anthropic", "model": "claude-a"},
    "naming_model": {"provider": "OpenAI", "model": "gpt-b"},
    "effort": "medium", "auto_approve_plan": True, "interactive": False, "confirm": False, "budget_usd": 5.0,
}


@pytest.fixture
def restored():
    before = (sdk.get_api_keys(), sdk.get_base_urls(), sdk.get_role_table(), sdk.get_default_model(),
              sdk.get_naming_model(), sdk.get_effort(), sdk.get_auto_approve_plan())
    yield
    sdk.set_api_keys(before[0])
    sdk.set_base_urls(before[1])
    sdk.set_role_table(before[2])
    sdk.set_default_model(before[3])
    sdk.set_naming_model(before[4])
    sdk.set_effort(before[5])
    sdk.set_auto_approve_plan(before[6])


def test_keys_and_base_urls_in_the_environment_reach_the_sdk_settings(restored):
    worker.configure({"DAEDALUS_API_KEYS": KEYS, "DAEDALUS_BASE_URLS": URLS})
    assert sdk.get_api_key_for_provider("Anthropic") == "sk-test-1"
    assert sdk.get_api_key_for_provider("OpenAI") == "sk-test-2"
    assert sdk.get_base_url_for_provider("Ollama") == "http://ollama:11434"


def test_a_worker_with_no_secrets_in_its_environment_configures_nothing(restored):
    sdk.set_api_keys([])
    worker.configure({})
    assert sdk.get_api_keys() == []


def test_the_settings_of_a_send_reach_the_sdk_and_the_registry(restored):
    worker.apply_settings(SETTINGS)
    assert sdk.get_role_table() == SETTINGS["role_table"]
    assert sdk.get_default_model() == "claude-a" and sdk.get_naming_model() == "gpt-b"
    assert sdk.get_effort() == "medium" and sdk.get_auto_approve_plan() is True
    assert sdk.get_model_entry("claude-a")["provider"] == "Anthropic"
    assert sdk.get_model_entry("gpt-b")["provider"] == "OpenAI"


def test_a_setting_the_send_does_not_name_is_left_as_it_was(restored):
    sdk.set_effort("low")
    worker.apply_settings({"budget_usd": 1.0})
    assert sdk.get_effort() == "low"


def test_without_settings_and_keys_the_engine_has_no_model_and_with_them_it_builds_one(restored, monkeypatch):
    sdk.set_api_keys([])
    sdk.set_default_model("")
    sdk.set_role_table([])
    with pytest.raises(ValueError, match="No model selected"):
        sdk.Client(kernel=sdk.NO_KERNEL).llm
    worker.configure({"DAEDALUS_API_KEYS": KEYS})
    worker.apply_settings(SETTINGS)
    assert sdk.Client(kernel=sdk.NO_KERNEL).llm is not None


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
