# FreeCadCopilot/utils/prompt_manager.py
"""
PromptManager – Singleton that manages system prompts.

Factory defaults are loaded from config/prompts.json (read-only at runtime).
User-created/edited prompts are stored in FreeCAD ParamGet via SettingsManager.
The effective prompt list is factory prompts merged with user prompts
(user wins on key collision, enabling full override of a factory default).
"""
import os
import json
from functools import lru_cache

from PySide.QtCore import QObject, Signal


def _load_factory_prompts() -> dict:
    """Load config/prompts.json from the addon root — never mutated."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "config", "prompts.json"
    )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        import FreeCAD
        FreeCAD.Console.PrintWarning(f"[FreeCadCopilot] Could not load prompts.json: {exc}\n")
        return {}


class _PromptManager(QObject):
    """Internal singleton implementation."""

    prompts_changed = Signal()  # emitted after any CRUD operation

    def __init__(self):
        super().__init__()
        self._factory: dict = _load_factory_prompts()

        # React to external user-prompt saves
        from utils import settings_manager
        settings_manager.instance().user_prompts_changed.connect(self.prompts_changed)

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_factory_prompts(self) -> dict:
        """Return factory-default prompts dict (read-only)."""
        return dict(self._factory)

    def get_user_prompts(self) -> dict:
        from utils import settings_manager
        return settings_manager.instance().get_user_prompts()

    def get_all_prompts(self) -> dict:
        """
        Returns merged dict: factory first, then user prompts (user wins).
        Each value is {name, text, is_factory: bool}.
        """
        result = {}
        for key, val in self._factory.items():
            result[key] = {**val, "is_factory": True}
        for key, val in self.get_user_prompts().items():
            result[key] = {**val, "is_factory": False}
        return result

    def get_prompt(self, key: str) -> dict | None:
        """Return a single prompt dict or None if not found."""
        return self.get_all_prompts().get(key)

    def get_prompt_text(self, key: str) -> str:
        """Convenience: return just the text for a given key, or '' if missing."""
        p = self.get_prompt(key)
        return p.get("text", "") if p else ""

    # ── Create / Update ───────────────────────────────────────────────────────

    def save_user_prompt(self, key: str, name: str, text: str):
        """
        Create or update a user-defined prompt.
        Keys must not be empty; auto-generated if blank using slugified name.
        """
        if not key:
            key = name.lower().replace(" ", "_").replace("-", "_")[:40]
        user = self.get_user_prompts()
        user[key] = {"name": name, "text": text}
        from utils import settings_manager
        settings_manager.instance().set_user_prompts(user)
        # prompts_changed will be emitted automatically via signal chain

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_user_prompt(self, key: str):
        """Delete a user-defined prompt.  Factory prompts cannot be deleted."""
        user = self.get_user_prompts()
        if key in user:
            del user[key]
            from utils import settings_manager
            settings_manager.instance().set_user_prompts(user)

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset_to_defaults(self):
        """Remove all user prompts; factory prompts are always present."""
        from utils import settings_manager
        settings_manager.instance().set_user_prompts({})


# ── Singleton accessor ────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def instance() -> _PromptManager:
    return _PromptManager()
