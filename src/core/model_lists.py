"""
Which providers' model lists are asked for, kept and dropped.

A provider is asked once it is configured and has no list; a credential change
drops only the lists fetched with the old credential. The ``fetch`` it is given
runs wherever the caller likes, so no thread or Qt object is needed here.
"""
from typing import NamedTuple

from core import model_catalog


class Ask(NamedTuple):
    provider: str
    api_key: str
    base_url: str


class ModelLists:
    def __init__(self, settings, fetch):
        self._settings = settings
        self._fetch = fetch
        self._landed: dict = {}
        self._in_flight: dict = {}

    def _credentials(self, provider: str):
        key = self._settings.get_api_key_for_provider(provider)
        base = self._settings.get_base_url_for_provider(provider)
        if not model_catalog.configured(provider, key, base):
            return None
        return Ask(provider, key, base)

    def _configured(self) -> list:
        return [provider for provider in self._settings.get_providers()
                if self._credentials(provider) is not None]

    def to_ask(self, provider: str | None = None) -> list:
        """The asks to start now: configured, not landed, not already in flight."""
        wanted = [provider] if provider else self._configured()
        asks = []
        for name in wanted:
            ask = self._credentials(name)
            if ask is None or name in self._landed or name in self._in_flight:
                continue
            self._in_flight[name] = ask
            asks.append(ask)
        return asks

    def fetch(self, ask: Ask) -> list:
        return self._fetch(ask.provider, ask.api_key, base_url=ask.base_url or None)

    def landed(self, ask: Ask, models: list) -> bool:
        """Keep *models* unless the credential they were fetched with has since changed."""
        if self._in_flight.get(ask.provider) is not ask:
            return False
        del self._in_flight[ask.provider]
        self._landed[ask.provider] = (ask, models)
        return True

    def failed(self, ask: Ask) -> bool:
        """Let *ask*'s provider be asked again, unless its credential has since changed."""
        if self._in_flight.get(ask.provider) is not ask:
            return False
        del self._in_flight[ask.provider]
        return True

    def keys_changed(self):
        for provider, ask in list(self._in_flight.items()):
            if ask != self._credentials(provider):
                del self._in_flight[provider]
        for provider, (ask, _) in list(self._landed.items()):
            if ask != self._credentials(provider):
                del self._landed[provider]

    def forget(self):
        self._landed.clear()
        self._in_flight.clear()

    def available(self) -> list:
        """``{id, display, provider}`` for every landed list of a configured provider."""
        return [{"id": m["id"], "display": m["display"], "provider": provider}
                for provider in self._configured() if provider in self._landed
                for m in self._landed[provider][1]]

    def awaiting(self) -> bool:
        """True until every configured provider has a landed list; a failed fetch is no answer."""
        return any(provider not in self._landed for provider in self._configured())
