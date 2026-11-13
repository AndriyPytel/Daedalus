"""
Level 1 -- a setting changed through `sdk`, as the UI now changes it, reaches
the engine and fires its bridge signal exactly once.
"""
import pytest


@pytest.fixture
def heard(the_settings_never_reach_the_user_s_freecad):
    from bridge import settings_manager

    manager = settings_manager.instance()
    got = {}
    for name in ("api_keys", "base_urls", "last_model"):
        got[name] = []
        getattr(manager, name + "_changed").connect(got[name].append)
    return got


def test_each_sdk_write_reaches_the_engine_and_emits_once(heard):
    import sdk

    keys = [{"provider": "Anthropic", "api_key": "k"}]
    urls = {"Home server": "http://localhost:1"}

    sdk.set_api_keys(keys)
    sdk.set_base_urls(urls)
    sdk.set_last_model("Anthropic", "l")

    assert (sdk.get_api_keys(), sdk.get_base_urls(), sdk.get_last_model()) == (
        keys, urls, ("Anthropic", "l"))
    assert heard["api_keys"] == [keys] and heard["base_urls"] == [urls]
    assert heard["last_model"].count(("Anthropic", "l")) == 1


def test_an_upsert_through_sdk_emits_once(heard):
    import sdk

    sdk.set_api_key_for_provider("OpenAI", "sk-1")
    sdk.set_base_url_for_provider("Home server", "http://localhost:2")

    assert sdk.get_api_key_for_provider("OpenAI") == "sk-1"
    assert sdk.get_base_url_for_provider("Home server") == "http://localhost:2"
    assert len(heard["api_keys"]) == 1
    assert len(heard["base_urls"]) == 1


def test_a_write_through_the_bridge_manager_emits_once(heard):
    from bridge import settings_manager

    settings_manager.instance().set_last_model("Anthropic", "viaBridge")

    assert heard["last_model"].count(("Anthropic", "viaBridge")) == 1
