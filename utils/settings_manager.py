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

    ``api_keys_changed`` is emitted after any key add, remove or edit;
    ``base_urls_changed`` after a local server's address changes;
    ``default_model_changed`` when the default model preference changes; and
    ``last_model_changed`` when the chat UI's last-used model changes.

    A local server's address is not a secret and is not a key. It lives in its
    own setting so it is never password-masked, never stored beside the keys,
    and a server that wants both an address and a token has room for both.
    """

    api_keys_changed    = Signal(list)
    base_urls_changed   = Signal(dict)
    default_model_changed = Signal(str)
    last_model_changed  = Signal(str)

    def __init__(self):
        super().__init__()

    @staticmethod
    def _grp():
        return FreeCAD.ParamGet(_PARAM_PATH)

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

    def get_default_model(self) -> str:
        return self._grp().GetString("DefaultModel", "")

    def set_default_model(self, model_id: str):
        self._grp().SetString("DefaultModel", model_id)
        self.default_model_changed.emit(model_id)

    def get_naming_model(self) -> str:
        """
        Which model names a Session. Empty means whichever the combo is on.

        Not a hardcoded cheap model: that assumes a key for one provider, so on
        a local-only or single-provider setup naming would silently never fire.
        """
        return self._grp().GetString("NamingModel", "")

    def set_naming_model(self, model_id: str):
        self._grp().SetString("NamingModel", model_id)

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

    def get_effort(self) -> str:
        """
        How hard the model is asked to think, as `core.llm.EFFORTS` spells it.

        Empty until the user picks one, and `core.llm` is what decides what
        empty means: the levels are its dial, and a value stored here that it
        no longer offers must not become a flag nobody can spell.
        """
        return self._grp().GetString("Effort", "")

    def set_effort(self, level: str):
        self._grp().SetString("Effort", level)

    def get_profile_model(self, profile: str) -> str:
        """
        The model *profile* (`thinking` or `doing`) is set to.

        Empty until the profile has one of its own, and empty falls back to
        `get_last_model` rather than to nothing: a user who has never split
        the two dials still has the one model they always had, on both
        profiles, with no dialog and no error.
        """
        return self._grp().GetString("ProfileModel:%s" % profile, "") or self.get_last_model()

    def set_profile_model(self, profile: str, model_id: str):
        self._grp().SetString("ProfileModel:%s" % profile, model_id)

    def get_profile_effort(self, profile: str) -> str:
        """
        *profile*'s own stored effort, or the old single `Effort` a user who
        has never split the two dials still has. `core.llm.effort_for_profile`
        is what turns whatever this returns -- including empty, or a level an
        older build stored -- into one of `core.llm.EFFORTS`.
        """
        return self._grp().GetString("ProfileEffort:%s" % profile, "") or self.get_effort()

    def set_profile_effort(self, profile: str, level: str):
        self._grp().SetString("ProfileEffort:%s" % profile, level)

    def get_llm_credentials(self, model_id: str = None):
        """
        Returns (provider, model_id, api_key) for the given model_id,
        or for the last-/default-model if model_id is None.
        Raises ValueError if not configured.

        A local server is configured by its address; the token is optional, so
        a missing key is only an error when there is no address either. A
        provider that holds its own credential has neither, and is not asked
        for one: the `claude` CLI is signed in to a subscription, and the key
        this would demand is one nothing reads.
        """
        from core import model_catalog
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
        if (not api_key and not self.get_base_url_for_provider(provider)
                and not model_catalog.needs_no_key(provider)):
            raise ValueError(
                f"No API key for provider '{provider}'. Add one in Preferences → API & Providers."
            )
        return provider, mid, api_key


@lru_cache(maxsize=None)
def instance() -> _SettingsManager:
    return _SettingsManager()
