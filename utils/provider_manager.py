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

    def __init__(self, provider: str, api_key: str, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._api_key = api_key

    def run(self):
        from core import model_catalog
        try:
            models = model_catalog.fetch(self._provider, self._api_key)
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
        from utils.settings_manager import SettingsManager
        SettingsManager.instance().api_keys_changed.connect(self._on_keys_changed)

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _ensure_fetched(self, provider: str):
        """Start a background fetch the first time a provider is asked for."""
        if provider in self._fetched or provider in self._threads or provider not in self._model_map:
            return
        from utils.settings_manager import SettingsManager
        key = SettingsManager.instance().get_api_key_for_provider(provider)
        if not key:
            return
        thread = _FetchThread(provider, key, self)
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
        FreeCAD.Console.PrintWarning(
            f"[FreeCadCopilot] Could not fetch models for {provider}: {error}\n"
        )

    def _known(self, provider: str) -> list:
        """Fetched entries when they have landed, models.json otherwise."""
        return self._fetched.get(provider) or self._model_map.get(provider, [])

    def _entries_for(self, provider: str) -> list:
        self._ensure_fetched(provider)
        return self._known(provider)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_keys_changed(self, _keys: list):
        self._fetched.clear()
        self._threads.clear()
        self.models_changed.emit(self.get_available_models())

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_available_models(self) -> list:
        """
        Return a list of model dicts for every provider that has a non-empty
        API key stored.  Each dict has keys: ``id``, ``display``, ``provider``.
        """
        from utils.settings_manager import SettingsManager
        sm = SettingsManager.instance()
        result = []
        for provider in self._model_map:
            if not sm.get_api_key_for_provider(provider):
                continue
            for m in self._entries_for(provider):
                result.append({"id": m["id"], "display": m["display"], "provider": provider})
        return result

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

    def test_connection(self, provider: str, api_key: str) -> tuple:
        """
        Synchronous connection test — the same request that fetches the model
        list, so a passing test guarantees a populated combo.  Returns
        (success: bool, message: str).  Call from a QThread to avoid blocking
        the UI.
        """
        from core import model_catalog
        try:
            models = model_catalog.fetch(provider, api_key)
        except Exception as exc:
            return False, str(exc)
        return True, f"Connection successful — {len(models)} models"


# ── Singleton accessor ────────────────────────────────────────────────────────

_instance: _ProviderManager = None


class ProviderManager:
    """Public singleton facade — use ``ProviderManager.instance()``."""

    @staticmethod
    def instance() -> _ProviderManager:
        global _instance
        if _instance is None:
            _instance = _ProviderManager()
        return _instance
