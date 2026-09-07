"""
SettingsManager – Qt-signal wrapper around `core.settings`'s persistence.

All components (Preferences UI, Chat Panel, Agents) talk to this class instead
of calling FreeCAD.ParamGet directly.  Qt signals let the Chat UI react to
preference changes in real-time without restarting FreeCAD. The persistence
itself -- every read and most writes -- lives in `core.settings`, which has no
`PySide` import; this class only adds the emit on top of the four writes a
signal is connected to.
"""
from functools import lru_cache

from PySide.QtCore import QObject, Signal

from core import settings as _settings


class _SettingsManager(QObject):
    """
    Internal singleton implementation.  Access via ``settings_manager.instance()``.

    ``api_keys_changed`` is emitted after any key add, remove or edit;
    ``base_urls_changed`` after a local server's address changes;
    ``default_model_changed`` when the default model preference changes; and
    ``last_model_changed`` when the chat UI's last-used model changes.

    Every other read and write is `core.settings.Settings`'s own method,
    reached here through `__getattr__` since it carries no signal to emit.
    """

    api_keys_changed    = Signal(list)
    base_urls_changed   = Signal(dict)
    default_model_changed = Signal(str)
    last_model_changed  = Signal(str)

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

    def set_default_model(self, model_id: str):
        self._persist.set_default_model(model_id)
        self.default_model_changed.emit(model_id)

    def set_last_model(self, model_id: str):
        self._persist.set_last_model(model_id)
        self.last_model_changed.emit(model_id)


@lru_cache(maxsize=None)
def instance() -> _SettingsManager:
    return _SettingsManager()
