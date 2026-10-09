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
    ``api_keys_changed``, ``base_urls_changed``, ``default_model_changed``,
    ``last_model_changed`` and ``role_table_changed`` fire after their matching
    write, whoever makes it -- `sdk` included -- and every read and write is
    `core.settings.Settings`'s own method, reached here through `__getattr__`.
    """

    api_keys_changed    = Signal(list)
    base_urls_changed   = Signal(dict)
    default_model_changed = Signal(str)
    last_model_changed  = Signal(str)
    role_table_changed  = Signal(list)

    def __init__(self):
        super().__init__()
        self._persist = _settings.instance()
        for name in ("api_keys", "base_urls", "default_model", "last_model", "role_table"):
            self._persist.subscribe(name, getattr(self, name + "_changed").emit)

    def __getattr__(self, name):
        return getattr(self._persist, name)


@lru_cache(maxsize=None)
def instance() -> _SettingsManager:
    return _SettingsManager()
