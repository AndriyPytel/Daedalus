# FreeCadCopilot/utils/provider_manager.py
"""
ProviderManager – Singleton that derives the live model list from the API keys
currently stored in SettingsManager.

Emits ``models_changed`` whenever the key list changes so the Chat UI can
refresh its model combo without a restart.
"""
import os
import json
from PySide.QtCore import QObject, Signal


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


class _ProviderManager(QObject):
    """Internal singleton implementation."""

    models_changed = Signal(list)  # list[dict] — [{display, id, provider}, …]

    def __init__(self):
        super().__init__()
        self._model_map: dict = _load_model_map()

        # Subscribe to key changes so the model list stays in sync
        from utils.settings_manager import SettingsManager
        SettingsManager.instance().api_keys_changed.connect(self._on_keys_changed)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_keys_changed(self, _keys: list):
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
        for provider, entries in self._model_map.items():
            if provider == "Custom/Local":
                key = sm.get_api_key_for_provider(provider)
                # Include custom even with empty key if explicitly added
                if key is not None and sm.get_api_key_for_provider(provider) != "":
                    for m in entries:
                        result.append({"id": m["id"], "display": m["display"], "provider": provider})
            else:
                key = sm.get_api_key_for_provider(provider)
                if key:
                    for m in entries:
                        result.append({"id": m["id"], "display": m["display"], "provider": provider})
        return result

    def get_model_entry(self, model_id: str) -> dict | None:
        """Find a model entry by its ``id`` across all providers."""
        for provider, entries in self._model_map.items():
            for m in entries:
                if m["id"] == model_id:
                    return {"id": m["id"], "display": m["display"], "provider": provider}
        return None

    def get_all_providers(self) -> list:
        """Return list of all provider names from models.json."""
        return list(self._model_map.keys())

    def get_models_for_provider(self, provider: str) -> list:
        """Return [{id, display}] for a given provider."""
        return list(self._model_map.get(provider, []))

    def test_connection(self, provider: str, api_key: str) -> tuple:
        """
        Synchronous connection test.  Returns (success: bool, message: str).
        Call from a QThread to avoid blocking the UI.
        """
        try:
            if provider == "OpenAI":
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                client.models.list()
                return True, "Connection successful"

            elif provider == "Google":
                import google.generativeai as genai
                genai.configure(api_key=api_key)
                list(genai.list_models())
                return True, "Connection successful"

            elif provider == "Anthropic":
                from anthropic import Anthropic
                client = Anthropic(api_key=api_key)
                client.models.list()
                return True, "Connection successful"

            elif provider == "Custom/Local":
                # For local, just check if the endpoint responds (key is base URL)
                import urllib.request
                req = urllib.request.urlopen(api_key.rstrip("/") + "/models", timeout=5)
                return req.status == 200, "Connection successful"

            else:
                return False, f"Unknown provider: {provider}"

        except Exception as exc:
            return False, str(exc)


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
