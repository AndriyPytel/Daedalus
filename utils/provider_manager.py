# FreeCadCopilot/utils/provider_manager.py
"""
ProviderManager – Singleton that derives the live model list from the API keys
currently stored in SettingsManager.

The list each key really entitles you to is fetched from the provider by
``core.model_catalog`` on a worker thread; ``config/models.json`` is the
offline fallback and the registry of which providers exist at all.

Emits ``models_changed`` whenever the key list or a fetched list changes so the
Chat UI can refresh its model combo without a restart.
"""
import os
import json
from functools import lru_cache

from PySide.QtCore import QObject, QThread, Signal


def _load_model_map() -> dict:
    """Load config/models.json from the addon root."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "models.json"
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"[FreeCadCopilot] Could not load models.json: {exc}\n")
        return {}


class _FetchThread(QThread):
    """Asks one provider for its model list, off the UI thread."""

    fetched = Signal(str, list)   # provider, [{id, display}]
    failed = Signal(str, str)     # provider, error

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
    """Internal singleton implementation."""

    models_changed = Signal(list)  # list[dict] — [{display, id, provider}, …]

    def __init__(self):
        super().__init__()
        self._model_map: dict = _load_model_map()
        # ponytail: session-lifetime cache; a disk cache with a TTL is the
        # upgrade path if one fetch per provider per session becomes noticeable.
        self._fetched: dict = {}   # provider -> [{id, display}]
        self._threads: dict = {}   # provider -> _FetchThread (also the "asked already" mark)

        # Subscribe to key changes so the model list stays in sync
        from utils import settings_manager
        settings_manager.instance().api_keys_changed.connect(self._on_keys_changed)
        # An address change invalidates a fetched list exactly as a key change does.
        settings_manager.instance().base_urls_changed.connect(self._on_keys_changed)

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _configured(self, provider: str) -> tuple:
        """
        (api_key, base_url) when *provider* is usable at all, else None.

        A local server is configured by its address and may want no token; a
        hosted provider is configured by its key and has no address to give.
        """
        from core import model_catalog
        from utils import settings_manager

        sm = settings_manager.instance()
        key = sm.get_api_key_for_provider(provider)
        base = sm.get_base_url_for_provider(provider)
        if model_catalog.needs_base_url(provider):
            return (key, base) if base else None
        return (key, base) if key else None

    def _ensure_fetched(self, provider: str):
        """Start a background fetch the first time a provider is asked for."""
        if provider in self._fetched or provider in self._threads or provider not in self._model_map:
            return
        credentials = self._configured(provider)
        if credentials is None:
            # Nothing to ask with. Not a failure and not worth a warning — the
            # user simply has not filled the page in yet.
            return
        key, base = credentials
        thread = _FetchThread(provider, key, base, self)
        thread.fetched.connect(self._on_fetched)
        thread.failed.connect(self._on_fetch_failed)
        self._threads[provider] = thread
        thread.start()

    def _on_fetched(self, provider: str, models: list):
        if self.sender() is not self._threads.get(provider):
            return  # a fetch outlived the key it was started for
        self._fetched[provider] = models
        self.models_changed.emit(self.get_available_models())

    def _on_fetch_failed(self, provider: str, error: str):
        import FreeCAD
        from utils import settings_manager

        # A local server's failure is about an address the user typed, and
        # "Connection refused" without it says nothing they can act on.
        where = settings_manager.instance().get_base_url_for_provider(provider)
        at = f" at {where}" if where else ""
        FreeCAD.Console.PrintWarning(
            f"[FreeCadCopilot] Could not fetch models for {provider}{at}: {error}\n"
        )

    def _known(self, provider: str) -> list:
        """Fetched entries when they have landed, models.json otherwise."""
        return self._fetched.get(provider) or self._model_map.get(provider, [])

    def _entries_for(self, provider: str) -> list:
        self._ensure_fetched(provider)
        return self._known(provider)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_keys_changed(self, _changed):
        self._fetched.clear()
        self._threads.clear()
        self.models_changed.emit(self.get_available_models())

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_available_models(self) -> list:
        """
        Return a list of model dicts for every provider that is configured —
        a key, or a server address for a local one.  Each dict has keys:
        ``id``, ``display``, ``provider``.
        """
        result = []
        for provider in self._model_map:
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
                   for provider in self._model_map
                   if self._configured(provider) is not None)

    def get_model_entry(self, model_id: str) -> dict | None:
        """Find a model entry by its ``id`` across all providers."""
        for provider in self._model_map:
            # Deliberately does not trigger a fetch: this runs on the Step thread.
            for m in self._known(provider):
                if m["id"] == model_id:
                    return {"id": m["id"], "display": m["display"], "provider": provider}
        return None

    def get_all_providers(self) -> list:
        """Return list of all provider names from models.json."""
        return list(self._model_map.keys())

    def get_models_for_provider(self, provider: str) -> list:
        """Return [{id, display}] for a given provider."""
        return list(self._entries_for(provider))

    def test_connection(self, provider: str, api_key: str, base_url: str = "") -> tuple:
        """
        Synchronous connection test — the same request that fetches the model
        list, so a passing test guarantees a populated combo.  Returns
        (success: bool, message: str).  Call from a QThread to avoid blocking
        the UI.
        """
        from core import model_catalog
        try:
            models = model_catalog.fetch(provider, api_key, base_url=base_url or None)
        except Exception as exc:
            return False, str(exc)
        return True, f"Connection successful — {len(models)} models"


# ── Singleton accessor ────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def instance() -> _ProviderManager:
    return _ProviderManager()
