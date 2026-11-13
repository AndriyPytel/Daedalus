from core.model_lists import ModelLists
from core.settings import Settings

HOME = "Home server"
ADDRESS = "http://localhost:11434"


class Fetching:
    def __init__(self):
        self.calls = []

    def __call__(self, provider, api_key, base_url=None):
        self.calls.append((provider, api_key, base_url))
        return [{"id": provider + "-model", "display": provider + " model"}]

    @property
    def providers(self):
        return [call[0] for call in self.calls]


def _settings(keys=(), addresses=None):
    settings = Settings()
    settings.set_api_keys([{"provider": name, "api_key": key} for name, key in keys])
    settings.set_base_urls(addresses or {})
    return settings


def _settle(lists):
    for ask in lists.to_ask():
        lists.landed(ask, lists.fetch(ask))


def test_a_hosted_provider_with_no_key_is_not_asked():
    fetching = Fetching()
    lists = ModelLists(_settings(keys=[("Anthropic", "sk-a")]), fetching)

    _settle(lists)

    assert "Anthropic" in fetching.providers
    assert "OpenAI" not in fetching.providers


def test_a_local_provider_with_an_address_and_no_key_is_asked():
    fetching = Fetching()
    lists = ModelLists(_settings(addresses={HOME: ADDRESS}), fetching)

    _settle(lists)

    assert (HOME, "", ADDRESS) in fetching.calls


def test_a_key_change_drops_only_that_providers_list():
    settings = _settings(keys=[("OpenAI", "sk-1"), ("Anthropic", "sk-a")])
    fetching = Fetching()
    lists = ModelLists(settings, fetching)
    _settle(lists)
    before = len(fetching.calls)

    settings.set_api_key_for_provider("OpenAI", "sk-2")
    lists.keys_changed()

    assert [m["provider"] for m in lists.available()
            if m["provider"] != "Claude (subscription)"] == ["Anthropic"]
    assert [ask.provider for ask in lists.to_ask()] == ["OpenAI"]
    assert len(fetching.calls) == before


def test_a_key_change_that_touches_no_list_asks_nothing_again():
    settings = _settings(keys=[("OpenAI", "sk-1")])
    lists = ModelLists(settings, Fetching())
    _settle(lists)

    settings.set_api_key_for_provider("Anthropic", "sk-a")
    lists.keys_changed()

    assert [ask.provider for ask in lists.to_ask()] == ["Anthropic"]


def test_lists_are_awaited_until_every_configured_provider_has_landed_or_failed():
    lists = ModelLists(_settings(keys=[("OpenAI", "sk-1"), ("Anthropic", "sk-a")]),
                       Fetching())
    asks = {ask.provider: ask for ask in lists.to_ask()}
    states = [lists.awaiting()]

    lists.landed(asks["OpenAI"], [])
    states.append(lists.awaiting())
    lists.failed(asks["Anthropic"])
    states.append(lists.awaiting())
    lists.landed(asks["Claude (subscription)"], [])
    states.append(lists.awaiting())
    retry = lists.to_ask()
    lists.landed(retry[0], [])
    states.append(lists.awaiting())

    assert [ask.provider for ask in retry] == ["Anthropic"]
    assert states == [True, True, True, True, False]


def test_a_provider_is_not_asked_while_its_fetch_is_in_flight():
    lists = ModelLists(_settings(keys=[("OpenAI", "sk-1")]), Fetching())

    first = lists.to_ask()

    assert "OpenAI" in [ask.provider for ask in first]
    assert lists.to_ask() == []


def test_a_failed_fetch_is_asked_again():
    lists = ModelLists(_settings(keys=[("OpenAI", "sk-1")]), Fetching())
    ask = next(ask for ask in lists.to_ask() if ask.provider == "OpenAI")

    lists.failed(ask)

    assert [a.provider for a in lists.to_ask()] == ["OpenAI"]


def test_a_list_that_outlived_its_key_is_not_kept():
    settings = _settings(keys=[("OpenAI", "sk-1")])
    lists = ModelLists(settings, Fetching())
    old = next(ask for ask in lists.to_ask() if ask.provider == "OpenAI")

    settings.set_api_key_for_provider("OpenAI", "sk-2")
    lists.keys_changed()

    assert not lists.landed(old, [{"id": "stale", "display": "Stale"}])
    assert all(m["id"] != "stale" for m in lists.available())


def test_a_stale_failure_leaves_the_fetch_in_flight():
    settings = _settings(keys=[("OpenAI", "sk-1")])
    lists = ModelLists(settings, Fetching())
    old = next(ask for ask in lists.to_ask() if ask.provider == "OpenAI")
    settings.set_api_key_for_provider("OpenAI", "sk-2")
    lists.keys_changed()
    new = next(ask for ask in lists.to_ask() if ask.provider == "OpenAI")

    assert not lists.failed(old)
    assert lists.landed(new, [])
