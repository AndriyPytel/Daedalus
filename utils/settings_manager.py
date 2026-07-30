# FreeCadCopilot/utils/settings_manager.py
"""
SettingsManager – Singleton that owns all persistent settings for FreeCadCopilot.

All components (Preferences UI, Chat Panel, Agents) talk to this class instead
of calling FreeCAD.ParamGet directly.  Qt signals let the Chat UI react to
preference changes in real-time without restarting FreeCAD.
"""
import json
import FreeCAD
from PySide.QtCore import QObject, Signal

_PARAM_PATH = "User parameter:BaseApp/Preferences/FreeCadCopilot"


class _SettingsManager(QObject):
    """
    Internal singleton implementation.  Access via ``SettingsManager.instance()``.
    """

    # ── Signals ──────────────────────────────────────────────────────────────
    api_keys_changed    = Signal(list)   # emitted after any key add/remove/edit
    default_model_changed = Signal(str)  # emitted when the default model pref changes
    last_model_changed  = Signal(str)    # emitted when the chat-UI last-used model changes

    def __init__(self):
        super().__init__()

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _grp():
        return FreeCAD.ParamGet(_PARAM_PATH)

    # ── API Keys ─────────────────────────────────────────────────────────────

    def get_api_keys(self) -> list:
        """Return list of {provider, api_key} dicts."""
        raw = self._grp().GetString("ApiKeys", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []

    def set_api_keys(self, keys: list):
        """Persist key list and emit signal."""
        self._grp().SetString("ApiKeys", json.dumps(keys))
        self.api_keys_changed.emit(keys)

    def get_api_key_for_provider(self, provider: str) -> str:
        """Return the stored API key string for *provider*, or '' if not found."""
        for entry in self.get_api_keys():
            if entry.get("provider") == provider:
                return entry.get("api_key", "")
        return ""

    def set_api_key_for_provider(self, provider: str, key: str):
        """Upsert a single provider's key and emit signal."""
        keys = self.get_api_keys()
        for entry in keys:
            if entry.get("provider") == provider:
                entry["api_key"] = key
                self.set_api_keys(keys)
                return
        keys.append({"provider": provider, "api_key": key})
        self.set_api_keys(keys)

    # ── Default model (set in Preferences) ──────────────────────────────────

    def get_default_model(self) -> str:
        return self._grp().GetString("DefaultModel", "")

    def set_default_model(self, model_id: str):
        self._grp().SetString("DefaultModel", model_id)
        self.default_model_changed.emit(model_id)

    # ── Last-used model (set by Chat UI, persists across sessions) ────────────

    def get_last_model(self) -> str:
        last = self._grp().GetString("LastUsedModel", "")
        if last:
            return last
        default = self.get_default_model()
        if default:
            return default
        # Legacy fallback
        return self._grp().GetString("SelectedModel", "")

    def set_last_model(self, model_id: str):
        self._grp().SetString("LastUsedModel", model_id)
        self.last_model_changed.emit(model_id)



    # ── Convenience: credentials for LLM ────────────────────────────────────

    def get_llm_credentials(self, model_id: str = None):
        """
        Returns (provider, model_id, api_key) for the given model_id,
        or for the last-/default-model if model_id is None.
        Raises ValueError if not configured.
        """
        from utils.provider_manager import ProviderManager
        mid = model_id or self.get_last_model()
        if not mid:
            raise ValueError("No model selected. Configure API keys in Preferences.")

        entry = ProviderManager.instance().get_model_entry(mid)
        if not entry:
            raise ValueError(f"Unknown model '{mid}'. Check your Preferences.")

        provider = entry["provider"]
        api_key  = self.get_api_key_for_provider(provider)
        if not api_key:
            raise ValueError(
                f"No API key for provider '{provider}'. Add one in Preferences → API & Providers."
            )
        return provider, mid, api_key


# ── Singleton accessor ────────────────────────────────────────────────────────

_instance: _SettingsManager = None


class SettingsManager:
    """Public singleton facade — use ``SettingsManager.instance()``."""

    @staticmethod
    def instance() -> _SettingsManager:
        global _instance
        if _instance is None:
            _instance = _SettingsManager()
        return _instance
