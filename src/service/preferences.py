"""
The preferences of an owner and the three levels that resolve into the settings of a Run: server defaults, then the owner's stored preferences, then the Run's own overrides, each level replacing the keys it names.

The schema forbids unknown keys, so a provider key or base URL, which are server secrets, is refused with 422 wherever preferences or overrides are read. The server defaults are `DEFAULTS` overlaid with the JSON object in `DAEDALUS_DEFAULTS`.
"""
import json
import os
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

DEFAULTS_VARIABLE = "DAEDALUS_DEFAULTS"
DEFAULTS = {"interactive": False, "confirm": False, "budget_usd": 5.0}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelChoice(Strict):
    provider: str
    model: str


class RoleRow(Strict):
    role: str
    provider: str
    model: str
    effort: Optional[str] = None


class Preferences(Strict):
    role_table: Optional[List[RoleRow]] = None
    default_model: Optional[ModelChoice] = None
    naming_model: Optional[ModelChoice] = None
    effort: Optional[str] = None
    interactive: Optional[bool] = None
    confirm: Optional[bool] = None
    budget_usd: Optional[float] = Field(None, gt=0)


def server_defaults():
    return {**DEFAULTS, **json.loads(os.environ.get(DEFAULTS_VARIABLE, "{}"))}


def resolve(stored, overrides=None):
    """The settings a Run runs with: the server defaults, replaced by *stored*, replaced by *overrides*."""
    named = {key: value for key, value in (overrides or {}).items() if value is not None}
    return {**server_defaults(), **stored, **named}


def given(preferences):
    return preferences.model_dump(exclude_unset=True)
