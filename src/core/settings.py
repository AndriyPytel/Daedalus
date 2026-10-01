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

from core.role_defaults import DEFAULT_ROLE_TABLE

_PARAM_PATH = "User parameter:BaseApp/Preferences/Daedalus"


class Settings:
    """
    The persistence half of the old `_SettingsManager`, reached via
    ``instance()``. A local server's address is not a secret and is not a
    key. It lives in its own setting so it is never password-masked, never
    stored beside the keys, and a server that wants both an address and a
    token has room for both.
    """

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

    def get_api_key_for_provider(self, provider: str) -> str:
        """Return the stored API key string for *provider*, or '' if not found."""
        for entry in self.get_api_keys():
            if entry.get("provider") == provider:
                return entry.get("api_key", "")
        return ""

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

    def get_base_url_for_provider(self, provider: str) -> str:
        """Return the stored server address for *provider*, or '' if not set."""
        return self.get_base_urls().get(provider, "")

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

    def get_default_model(self) -> str:
        return self._grp().GetString("DefaultModel", "")

    def set_default_model(self, model_id: str):
        self._grp().SetString("DefaultModel", model_id)

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

    def get_auto_approve_plan(self) -> bool:
        """
        Whether the Plan Gate auto-approves instead of stopping the run for
        the user, `core/loop.py :: _run` reusing the same standing-approval
        path a user's own APPROVE-and-stand answer already takes.

        Off until the user turns it on: an unattended run is opt-in, not
        the default a run silently becomes.
        """
        return self._grp().GetBool("AutoApprovePlan", False)

    def set_auto_approve_plan(self, enabled: bool):
        self._grp().SetBool("AutoApprovePlan", bool(enabled))

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

    def get_role_table(self) -> list:
        """One row per role -- `provider`, `model`, `effort` -- seeded with defaults."""
        raw = self._grp().GetString("RoleModelTable", "")
        if raw:
            try:
                stored = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                stored = None
            if isinstance(stored, list):
                return stored
        return [dict(row) for row in DEFAULT_ROLE_TABLE]

    def set_role_table(self, table: list):
        self._grp().SetString("RoleModelTable", json.dumps(table))

    def get_role_model(self, role: str) -> dict:
        """The stored row for *role*, or `None` if the table has no row for it."""
        for row in self.get_role_table():
            if row.get("role") == role:
                return row
        return None

    def get_llm_credentials(self, model_id: str = None):
        """
        Returns (provider, model_id, api_key) for the given model_id, or for
        the last-/default-model if model_id is None, raising ValueError if
        not configured. A local server is configured by its address; the
        token is optional, so a missing key is only an error when there is
        no address either. A provider that holds its own credential has
        neither and is not asked for one, since the `claude` CLI is signed
        in to a subscription and the key this would demand is one nothing
        reads.
        """
        from core import model_catalog
        from core import model_registry
        mid = model_id or self.get_last_model()
        if not mid:
            raise ValueError("No model selected. Configure API keys in Preferences.")

        entry = model_registry.get_model_entry(mid)
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
def instance() -> Settings:
    return Settings()
