"""
SettingsManager -- Qt-signal wrapper around `core.settings`'s persistence,
so every component (Preferences UI, Chat Panel, Agents) talks to this class
instead of calling FreeCAD.ParamGet directly. Its signals let the Chat UI
react to preference changes in real time without restarting FreeCAD. The
persistence itself -- every read and most writes -- lives in `core.settings`,
which has no `PySide` import; this class only adds the emit on top of the
writes a signal is connected to.
"""
from functools import lru_cache

from PySide.QtCore import QObject, Signal

from core import settings as _settings


class _SettingsManager(QObject):
    """
    Internal singleton implementation, reached via ``settings_manager.instance()``.
    ``api_keys_changed``, ``base_urls_changed``, ``default_model_changed`` and
    ``last_model_changed`` each fire after their matching write, and every
    other read and write is `core.settings.Settings`'s own method, reached
    here through `__getattr__` since it carries no signal to emit.
    """

    api_keys_changed    = Signal(list)
    base_urls_changed   = Signal(dict)
    default_model_changed = Signal(str)
    last_model_changed  = Signal(str)
    role_table_changed  = Signal(list)

    def __init__(self):
        super().__init__()
        self._persist = _settings.instance()

    def __getattr__(self, name):
        return getattr(self._persist, name)

    def set_api_keys(self, keys: list):
        """Persist key list and emit signal."""
        self._persist.set_api_keys(keys)
        self.api_keys_changed.emit(keys)

    def set_api_key_for_provider(self, provider: str, key: str):
        """Upsert a single provider's key and emit signal."""
        keys = self._persist.get_api_keys()
        for entry in keys:
            if entry.get("provider") == provider:
                entry["api_key"] = key
                self.set_api_keys(keys)
                return
        keys.append({"provider": provider, "api_key": key})
        self.set_api_keys(keys)

    def set_base_urls(self, urls: dict):
        """Persist the address map and emit signal."""
        self._persist.set_base_urls(urls)
        self.base_urls_changed.emit(urls)

    def set_base_url_for_provider(self, provider: str, url: str):
        """Upsert a single provider's address, or drop it when *url* is empty."""
        urls = self._persist.get_base_urls()
        url = (url or "").strip()
        if url:
            urls[provider] = url
        else:
            urls.pop(provider, None)
        self.set_base_urls(urls)

    def set_role_table(self, table: list):
        """Persist the role table and emit signal."""
        self._persist.set_role_table(table)
        self.role_table_changed.emit(table)

    def set_default_model(self, model_id: str):
        self._persist.set_default_model(model_id)
        self.default_model_changed.emit(model_id)

    def set_last_model(self, model_id: str):
        self._persist.set_last_model(model_id)
        self.last_model_changed.emit(model_id)


@lru_cache(maxsize=None)
def instance() -> _SettingsManager:
    return _SettingsManager()
