"""
ProviderManager – Singleton that derives the live model list from the API keys
currently stored in ``core.settings``.

The list each key really entitles you to is fetched from the provider by
``core.model_catalog`` on a worker thread; which providers exist is
``core.settings``'s answer, the catalog's plus the ones the user added.

Emits ``models_changed`` whenever the key list or a fetched list changes so the
Chat UI can refresh its model combo without a restart.
"""
from functools import lru_cache

from PySide.QtCore import QObject, QThread, Signal

from core import model_catalog, settings
from core.model_lists import Ask, ModelLists


class _FetchThread(QThread):
    """
    Asks one provider for its model list, off the UI thread.

    ``fetched`` carries the ask and its ``[{id, display}]`` list; ``failed``
    carries the ask and the error text.
    """

    fetched = Signal(object, list)
    failed = Signal(object, str)

    def __init__(self, lists: ModelLists, ask: Ask, parent=None):
        super().__init__(parent)
        self._lists = lists
        self._ask = ask

    def run(self):
        try:
            models = self._lists.fetch(self._ask)
        except Exception as exc:
            self.failed.emit(self._ask, str(exc))
            return
        self.fetched.emit(self._ask, models)


def _fetch(provider, api_key, base_url=None):
    return model_catalog.fetch(provider, api_key, base_url=base_url)


class _ProviderManager(QObject):
    """
    Internal singleton implementation. ``models_changed`` carries the whole
    list of ``{display, id, provider}`` dicts that ``core.model_lists`` holds
    for the session (a disk cache with a TTL is the upgrade path once one
    fetch per provider per session is noticeable).
    """

    models_changed = Signal(list)

    def __init__(self):
        super().__init__()
        persisted = settings.instance()
        self._lists = ModelLists(persisted, _fetch)
        persisted.subscribe("api_keys", self._keys_changed)
        persisted.subscribe("base_urls", self._keys_changed)

    def _start_fetches(self, provider: str | None = None):
        """
        Start a background fetch for each provider asked for the first time.

        A provider with nothing to ask with is skipped silently: that is not a
        failure and not worth a warning, the user simply has not filled the
        page in yet.
        """
        for ask in self._lists.to_ask(provider):
            thread = _FetchThread(self._lists, ask, self)
            thread.fetched.connect(self._list_landed)
            thread.failed.connect(self._on_fetch_failed)
            thread.start()

    def _list_landed(self, ask: Ask, models: list):
        if self._lists.landed(ask, models):
            self.models_changed.emit(self.get_available_models())

    def _on_fetch_failed(self, ask: Ask, error: str):
        """
        Warn that a provider could not be reached, naming the address.

        A local server's failure is about an address the user typed, and
        "Connection refused" without it says nothing they can act on.
        """
        import FreeCAD

        if not self._lists.failed(ask):
            return
        at = f" at {ask.base_url}" if ask.base_url else ""
        FreeCAD.Console.PrintWarning(
            f"[Daedalus] Could not fetch models for {ask.provider}{at}: {error}\n"
        )

    def _keys_changed(self, _changed):
        self._lists.keys_changed()
        self.models_changed.emit(self.get_available_models())

    def get_available_models(self) -> list:
        """
        Return a list of model dicts for every provider that is configured —
        a key, or a server address for a local one.  Each dict has keys:
        ``id``, ``display``, ``provider``.
        """
        self._start_fetches()
        return self._lists.available()

    def awaiting_model_lists(self) -> bool:
        """
        True until every configured provider has delivered its real model list.

        A provider that could not be reached has not said any model of its own
        is gone, so a failed fetch leaves it awaited.
        """
        return self._lists.awaiting()

    def get_all_providers(self) -> list:
        """Return list of all provider names the addon can reach."""
        return settings.instance().get_providers()

    def get_models_for_provider(self, provider: str) -> list:
        """Return [{id, display}] for a given provider."""
        self._start_fetches(provider)
        return [{"id": m["id"], "display": m["display"]}
                for m in self._lists.available() if m["provider"] == provider]

    def test_connection(self, provider: str, api_key: str, base_url: str = "") -> tuple:
        """
        Synchronous connection test, the same request that fetches the model
        list, so a passing test guarantees a populated combo. Returns
        (success: bool, message: str); call from a QThread to avoid blocking
        the UI. A provider with no key has no such request to make, so the
        test instead proves which account the CLI is signed in as.
        """
        if model_catalog.needs_no_key(provider):
            from core import llm
            return llm.signed_in()

        try:
            models = model_catalog.fetch(provider, api_key, base_url=base_url or None)
        except Exception as exc:
            return False, str(exc)
        return True, f"Connection successful — {len(models)} models"


@lru_cache(maxsize=None)
def instance() -> _ProviderManager:
    return _ProviderManager()
