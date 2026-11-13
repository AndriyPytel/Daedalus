"""
Settings -- Qt-free persistence for Daedalus's preferences.

Everything here reads and writes through `FreeCAD.ParamGet` and nothing else:
no `PySide` import, so any `core/` module can read or write a setting without
pulling in Qt at load time. `bridge/settings_manager.py` wraps this class in a
`QObject` for the call sites that connect to a change signal; every plain
read/write goes through here directly.
"""
import json
from functools import lru_cache

AUTO = "auto"

_PARAM_PATH = "User parameter:BaseApp/Preferences/Daedalus"
_LEGACY_PARAM_PATH = "User parameter:BaseApp/Preferences/FreeCadCopilot"
_SETTERS = {"String": "SetString", "Boolean": "SetBool", "Integer": "SetInt",
            "Unsigned Long": "SetUnsigned", "Float": "SetFloat"}


def migrate_legacy_preferences(old, new):
    """
    Copy every entry and nested group of `old` into `new` when `new` is empty.

    A user who set the add-on up under its former name keeps the keys
    and sessions; a `new` that already holds anything is never overwritten.
    """
    if new.GetContents() or new.GetGroups():
        return
    for kind, name, value in old.GetContents() or ():
        getattr(new, _SETTERS[kind])(name, value)
    for name in old.GetGroups() or ():
        migrate_legacy_preferences(old.GetGroup(name), new.GetGroup(name))


class Settings:
    """
    The persistence half of the old `_SettingsManager`, reached via
    ``instance()``. A local server's address is not a secret and is not a
    key. It lives in its own setting so it is never password-masked, never
    stored beside the keys, and a server that wants both an address and a
    token has room for both.
    """

    def __init__(self):
        self._listeners = {}

    def subscribe(self, name, callback):
        """Call *callback(value)* after each write of the setting *name* (`api_keys`, `base_urls`, `last_model`)."""
        self._listeners.setdefault(name, []).append(callback)

    def _notify(self, name, value):
        for callback in self._listeners.get(name, ()):
            callback(value)

    @staticmethod
    def _grp():
        import FreeCAD

        return FreeCAD.ParamGet(_PARAM_PATH)

    def get_api_keys(self) -> list:
        """Return list of {provider, api_key} dicts."""
        raw = self._grp().GetString("ApiKeys", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []

    def set_api_keys(self, keys: list):
        self._grp().SetString("ApiKeys", json.dumps(keys))
        self._notify("api_keys", keys)

    def get_api_key_for_provider(self, provider: str) -> str:
        """Return the stored API key string for *provider*, or '' if not found."""
        for entry in self.get_api_keys():
            if entry.get("provider") == provider:
                return entry.get("api_key", "")
        return ""

    def set_api_key_for_provider(self, provider: str, key: str):
        """Upsert a single provider's key."""
        keys = self.get_api_keys()
        for entry in keys:
            if entry.get("provider") == provider:
                entry["api_key"] = key
                break
        else:
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
        self._grp().SetString("BaseUrls", json.dumps(urls))
        self._notify("base_urls", urls)

    def set_base_url_for_provider(self, provider: str, url: str):
        """Upsert a single provider's address, or drop it when *url* is empty."""
        urls = self.get_base_urls()
        url = (url or "").strip()
        if url:
            urls[provider] = url
        else:
            urls.pop(provider, None)
        self.set_base_urls(urls)

    def get_base_url_for_provider(self, provider: str) -> str:
        """Return the stored server address for *provider*, or '' if not set."""
        return self.get_base_urls().get(provider, "")

    def get_providers(self) -> list:
        """The catalog's providers, then each name the user added on the Providers page and stored a key or an address under."""
        from core import model_catalog

        known = model_catalog.providers()
        stored = [entry.get("provider") for entry in self.get_api_keys()] + list(self.get_base_urls())
        return known + [name for name in dict.fromkeys(stored) if name and name not in known]

    def get_sessions(self) -> list:
        """
        The Session index: name, created_at, document, and the log's path.

        Only the index lives here. The transcript stays where the event stream
        already writes it -- one JSON object per line, appended per event.
        """
        raw = self._grp().GetString("Sessions", "[]")
        try:
            sessions = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
        return sessions if isinstance(sessions, list) else []

    def set_sessions(self, sessions: list):
        self._grp().SetString("Sessions", json.dumps(sessions))

    def get_last_model(self) -> tuple:
        """The `(provider, model)` last picked in the chat, or `("", "")` before any pick."""
        try:
            stored = json.loads(self._grp().GetString("LastModel", ""))
            return stored["provider"], stored["model"]
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            return "", ""

    def set_last_model(self, provider: str, model: str):
        self._grp().SetString("LastModel", json.dumps({"provider": provider, "model": model}))
        self._notify("last_model", (provider, model))

    def get_effort(self) -> str:
        """
        How hard the model is asked to think, as `core.llm.EFFORTS` spells it.

        Empty until the user picks one, and `core.llm` is what decides what
        empty means: the levels are its dial, and a value stored here that it
        does not offer must not become a flag nobody can spell.
        """
        return self._grp().GetString("Effort", "")

    def set_effort(self, level: str):
        self._grp().SetString("Effort", level)

    def get_domain(self) -> str:
        """The Domain the user picked in the panel, or `auto` where none was picked."""
        return self._grp().GetString("Domain", "") or AUTO

    def set_domain(self, choice: str):
        self._grp().SetString("Domain", choice)


@lru_cache(maxsize=None)
def instance() -> Settings:
    import FreeCAD

    migrate_legacy_preferences(FreeCAD.ParamGet(_LEGACY_PARAM_PATH), FreeCAD.ParamGet(_PARAM_PATH))
    return Settings()
