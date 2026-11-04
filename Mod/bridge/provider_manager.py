"""
ProviderManager – Singleton that derives the live model list from the API keys
currently stored in SettingsManager.

The list each key really entitles you to is fetched from the provider by
``core.model_catalog`` on a worker thread; which providers exist is
``core.settings``'s answer, the catalog's plus the ones the user added.

Emits ``models_changed`` whenever the key list or a fetched list changes so the
Chat UI can refresh its model combo without a restart.
"""
from functools import lru_cache

from PySide.QtCore import QObject, QThread, Signal


class _FetchThread(QThread):
    """
    Asks one provider for its model list, off the UI thread.

    ``fetched`` carries the provider name and its ``[{id, display}]`` list;
    ``failed`` carries the provider name and the error text.
    """

    fetched = Signal(str, list)
    failed = Signal(str, str)

    def __init__(self, provider: str, api_key: str, base_url: str = "", parent=None):
        super().__init__(parent)
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url

    def run(self):
        from core import model_catalog
        try:
            models = model_catalog.fetch(self._provider, self._api_key,
                                         base_url=self._base_url or None)
        except Exception as exc:
            self.failed.emit(self._provider, str(exc))
            return
        self.fetched.emit(self._provider, models)


class _ProviderManager(QObject):
    """
    Internal singleton implementation. ``models_changed`` carries the whole
    list of ``{display, id, provider}`` dicts, from a session-lifetime cache
    keyed by provider (a disk cache with a TTL is the upgrade path once one
    fetch per provider per session is noticeable). ``_resolved`` maps a model
    id to the ``(provider, display)`` that last reported it and survives a
    cleared fetch, because between a key change and the new list landing it
    is the only thing that can resolve a model for ``get_model_entry``, which
    runs on the Step thread and cannot itself ask.
    """

    models_changed = Signal(list)

    def __init__(self):
        super().__init__()
        self._fetched: dict = {}
        self._threads: dict = {}
        self._fetched_with: dict = {}
        self._resolved: dict = {}

        from bridge import settings_manager
        settings_manager.instance().api_keys_changed.connect(self._on_keys_changed)
        settings_manager.instance().base_urls_changed.connect(self._on_keys_changed)

    @property
    def _providers(self) -> list:
        """Read on every call, since a provider the user adds is a key or an address saved under a new name."""
        from bridge import settings_manager

        return settings_manager.instance().get_providers()

    def _configured(self, provider: str) -> tuple:
        """
        (api_key, base_url) when *provider* is usable at all, else None. A
        local server is configured by its address and may want no token; a
        hosted provider is configured by its key. A provider that holds its
        own credential is configured by having the CLI signed in, which this
        page never stores, so its Test button is what says whether it really is.
        """
        from core import model_catalog
        from bridge import settings_manager

        sm = settings_manager.instance()
        key = sm.get_api_key_for_provider(provider)
        base = sm.get_base_url_for_provider(provider)
        if model_catalog.needs_no_key(provider):
            return ("", "")
        if model_catalog.needs_base_url(provider):
            return (key, base) if base else None
        return (key, base) if key else None

    def _ensure_fetched(self, provider: str):
        """
        Start a background fetch the first time a provider is asked for.

        A provider with nothing to ask with is skipped silently: that is not a
        failure and not worth a warning, the user simply has not filled the
        page in yet.
        """
        if provider in self._fetched or provider in self._threads or provider not in self._providers:
            return
        credentials = self._configured(provider)
        if credentials is None:
            return
        key, base = credentials
        thread = _FetchThread(provider, key, base, self)
        thread.fetched.connect(self._on_fetched)
        thread.failed.connect(self._on_fetch_failed)
        self._threads[provider] = thread
        thread.start()

    def _on_fetched(self, provider: str, models: list):
        """
        Take a landed model list, unless the fetch outlived the key it was
        started for.
        """
        if self.sender() is not self._threads.get(provider):
            return
        self._fetched[provider] = models
        self._fetched_with[provider] = self._configured(provider)
        self._remember(provider, models)
        self.models_changed.emit(self.get_available_models())

    def _remember(self, provider: str, models: list):
        """
        Where each of these models came from, kept past the next key change.
        A model the provider stops reporting stays remembered, so a run
        against it fails at the provider rather than here. Also recorded on
        `core.model_registry`, which `core.settings` reads instead of
        reaching back into this Qt-touching cache.
        """
        from core import model_registry

        for model in models:
            self._resolved[model["id"]] = (provider, model["display"])
            model_registry.remember(model["id"], provider, model["display"])

    def _on_fetch_failed(self, provider: str, error: str):
        """
        Warn that a provider could not be reached, naming the address.

        A local server's failure is about an address the user typed, and
        "Connection refused" without it says nothing they can act on. A fetch
        that outlived its key is ignored, so it cannot drop the fetch in flight.
        """
        import FreeCAD
        from bridge import settings_manager

        if self.sender() is not self._threads.get(provider):
            return
        self._threads.pop(provider, None)
        where = settings_manager.instance().get_base_url_for_provider(provider)
        at = f" at {where}" if where else ""
        FreeCAD.Console.PrintWarning(
            f"[Daedalus] Could not fetch models for {provider}{at}: {error}\n"
        )

    def _known(self, provider: str) -> list:
        """What the provider last reported, or nothing until it has."""
        return self._fetched.get(provider, [])

    def _entries_for(self, provider: str) -> list:
        self._ensure_fetched(provider)
        return self._known(provider)

    def _on_keys_changed(self, _changed):
        """
        Drop the lists the change actually invalidated, and no others. A key
        change invalidates only the list that key entitled you to, and the
        signal carries the whole map rather than which entry moved, so the
        check is against what each list was fetched with.
        """
        for provider in list(self._providers):
            if self._fetched_with.get(provider) == self._configured(provider):
                continue
            self._fetched.pop(provider, None)
            self._fetched_with.pop(provider, None)
            self._threads.pop(provider, None)
        self.models_changed.emit(self.get_available_models())

    def get_available_models(self) -> list:
        """
        Return a list of model dicts for every provider that is configured —
        a key, or a server address for a local one.  Each dict has keys:
        ``id``, ``display``, ``provider``.
        """
        result = []
        for provider in self._providers:
            if self._configured(provider) is None:
                continue
            for m in self._entries_for(provider):
                result.append({"id": m["id"], "display": m["display"], "provider": provider})
        return result

    def awaiting_model_lists(self) -> bool:
        """
        True until every configured provider has delivered its real model list.

        A fetch that failed never lands in ``_fetched``, so its provider stays
        awaited: a provider that could not be reached has not said any model of
        its own is gone.
        """
        return any(provider not in self._fetched
                   for provider in self._providers
                   if self._configured(provider) is not None)

    def get_model_entry(self, model_id: str) -> dict | None:
        """
        Find a model entry by its ``id`` across all providers, falling back to
        what a provider last reported. A model that resolved once keeps its
        provider until a fetch says otherwise, so a fetched-only model stays
        resolvable between a key change and the new list landing. Deliberately
        does not trigger a fetch: this runs on the Step thread.
        """
        for provider in self._providers:
            for m in self._known(provider):
                if m["id"] == model_id:
                    return {"id": m["id"], "display": m["display"], "provider": provider}

        remembered = self._resolved.get(model_id)
        if remembered:
            provider, display = remembered
            return {"id": model_id, "display": display, "provider": provider}
        return None

    def get_all_providers(self) -> list:
        """Return list of all provider names the addon can reach."""
        return list(self._providers)

    def get_models_for_provider(self, provider: str) -> list:
        """Return [{id, display}] for a given provider."""
        return list(self._entries_for(provider))

    def test_connection(self, provider: str, api_key: str, base_url: str = "") -> tuple:
        """
        Synchronous connection test, the same request that fetches the model
        list, so a passing test guarantees a populated combo. Returns
        (success: bool, message: str); call from a QThread to avoid blocking
        the UI. A provider with no key has no such request to make, so the
        test instead proves which account the CLI is signed in as.
        """
        from core import model_catalog
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
