"""The LLM client seam.

Three implementations: LiteLLM (ADR-0004), `claude` CLI (ADR-0025), and a
scripted client. Everything above this line is testable without a network.

`EFFORTS` default is `medium`: on one sheet through Design `high` spent
41,704 tokens of thinking against `medium`'s 26,117 and four minutes more.
`profile_of` is the two-way split: `thinking` for Design/Plan, `doing` for
Execute/Assertion/Review.
"""

from core.llm.types import REASONING, TEXT, Chunk
from core.llm.exceptions import ProviderRefused, StreamCutOff
from core.llm.effort_policy import EFFORTS, PROFILES, CALL_TIMEOUT, profile_of
from core.role_defaults import ROLES, DEFAULT_ROLE_TABLE
from core.llm.session import Session
from core.llm.client import LiteLLMClient
from core.llm.claude import ClaudeCodeClient, find_claude, signed_in
from core.llm.scripted import ScriptedLLMClient
from core.llm.roles import ProfiledClient, RoleClient
from core.llm.attachments import (
    IMAGE_SUFFIXES,
    PDF_SUFFIXES,
    cache_control_supported,
    litellm_model_name,
    accepts_images,
    accepts_pdf,
    unusable_attachments,
)
from core.llm.stream import _billed

_FACTORY_NAMES = (
    "effort",
    "effort_for_profile",
    "effort_is_honoured",
    "selected_model_name",
    "client_from_settings",
    "client_from_profiles",
    "client_from_roles",
)


def __getattr__(name):
    if name in _FACTORY_NAMES:
        from core.llm import factory
        return getattr(factory, name)
    raise AttributeError(f"module 'core.llm' has no attribute {name!r}")


__all__ = [
    "REASONING",
    "TEXT",
    "Chunk",
    "ProviderRefused",
    "StreamCutOff",
    "EFFORTS",
    "PROFILES",
    "CALL_TIMEOUT",
    "profile_of",
    "ROLES",
    "DEFAULT_ROLE_TABLE",
    "Session",
    "LiteLLMClient",
    "ClaudeCodeClient",
    "find_claude",
    "signed_in",
    "ScriptedLLMClient",
    "ProfiledClient",
    "RoleClient",
    "IMAGE_SUFFIXES",
    "PDF_SUFFIXES",
    "cache_control_supported",
    "litellm_model_name",
    "accepts_images",
    "accepts_pdf",
    "unusable_attachments",
    "effort",
    "effort_for_profile",
    "effort_is_honoured",
    "selected_model_name",
    "client_from_settings",
    "client_from_profiles",
    "client_from_roles",
]
