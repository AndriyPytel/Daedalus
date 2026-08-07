# FreeCadCopilot/utils/settings_manager.py
"""
SettingsManager – Singleton that owns all persistent settings for FreeCadCopilot.

All components (Preferences UI, Chat Panel, Agents) talk to this class instead
of calling FreeCAD.ParamGet directly.  Qt signals let the Chat UI react to
preference changes in real-time without restarting FreeCAD.
"""
import json
from functools import lru_cache

import FreeCAD
from PySide.QtCore import QObject, Signal

_PARAM_PATH = "User parameter:BaseApp/Preferences/FreeCadCopilot"


class _SettingsManager(QObject):
    """
    Internal singleton implementation.  Access via ``settings_manager.instance()``.
    """

    # ── Signals ──────────────────────────────────────────────────────────────
    api_keys_changed    = Signal(list)   # emitted after any key add/remove/edit
    base_urls_changed   = Signal(dict)   # emitted after a local server's address changes
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

    # ── Base URLs ────────────────────────────────────────────────────────────
    #
    # A local server's address is not a secret and is not a key. It lives in its
    # own setting so it is never password-masked, never stored beside the keys,
    # and a server that wants both an address and a token has room for both
    # (FCC-50).

    def get_base_urls(self) -> dict:
        """Return {provider: base_url} for every provider that has an address."""
        raw = self._grp().GetString("BaseUrls", "{}")
        try:
            stored = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return stored if isinstance(stored, dict) else {}

    def set_base_urls(self, urls: dict):
        """Persist the address map and emit signal."""
        self._grp().SetString("BaseUrls", json.dumps(urls))
        self.base_urls_changed.emit(urls)

    def get_base_url_for_provider(self, provider: str) -> str:
        """Return the stored server address for *provider*, or '' if not set."""
        return self.get_base_urls().get(provider, "")

    def set_base_url_for_provider(self, provider: str, url: str):
        """Upsert a single provider's address, or drop it when *url* is empty."""
        urls = self.get_base_urls()
        url = (url or "").strip()
        if url:
            urls[provider] = url
        else:
            urls.pop(provider, None)
        self.set_base_urls(urls)

    # ── Sessions ─────────────────────────────────────────────────────────────

    def get_sessions(self) -> list:
        """
        The Session index: name, created_at, document, and the log's path.

        Only the index lives here. The transcript stays where the event stream
        already writes it — one JSON object per line, appended per event.
        """
        raw = self._grp().GetString("Sessions", "[]")
        try:
            sessions = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
        return sessions if isinstance(sessions, list) else []

    def set_sessions(self, sessions: list):
        self._grp().SetString("Sessions", json.dumps(sessions))

    # ── Default model (set in Preferences) ──────────────────────────────────

    def get_default_model(self) -> str:
        return self._grp().GetString("DefaultModel", "")

    def set_default_model(self, model_id: str):
        self._grp().SetString("DefaultModel", model_id)
        self.default_model_changed.emit(model_id)

    # ── Naming model (set in Preferences) ───────────────────────────────────

    def get_naming_model(self) -> str:
        """
        Which model names a Session. Empty means whichever the combo is on.

        Not a hardcoded cheap model: that assumes a key for one provider, so on
        a local-only or single-provider setup naming would silently never fire.
        """
        return self._grp().GetString("NamingModel", "")

    def set_naming_model(self, model_id: str):
        self._grp().SetString("NamingModel", model_id)

    # ── Last-used model (set by Chat UI, persists across sessions) ────────────

    def get_last_model(self) -> str:
        last = self._grp().GetString("LastUsedModel", "")
        if last:
            return last
        default = self.get_default_model()
        if default:
            return default
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
        from utils import provider_manager
        mid = model_id or self.get_last_model()
        if not mid:
            raise ValueError("No model selected. Configure API keys in Preferences.")

        entry = provider_manager.instance().get_model_entry(mid)
        if not entry:
            raise ValueError(
                f"'{mid}' is not a model any configured provider reports. Pick one from "
                f"the model list in the panel, or check the keys in "
                f"Preferences → API & Providers."
            )

        provider = entry["provider"]
        api_key  = self.get_api_key_for_provider(provider)
        if not api_key and not self.get_base_url_for_provider(provider):
            # A local server is configured by its address; the token is optional.
            raise ValueError(
                f"No API key for provider '{provider}'. Add one in Preferences → API & Providers."
            )
        return provider, mid, api_key


# ── Singleton accessor ────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def instance() -> _SettingsManager:
    return _SettingsManager()
