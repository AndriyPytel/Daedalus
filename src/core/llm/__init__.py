"""
The LLM client seam: three implementations, LiteLLM, `claude` CLI, and a
scripted client, with everything above this line testable without a
network. `EFFORTS` default is `medium`: on one sheet through Design `high`
spent 41,704 tokens of thinking against `medium`'s 26,117 and four minutes
more.
"""

from core.llm.types import REASONING, TEXT, Chunk
from core.llm.exceptions import CallStalled, ProviderRefused, StreamCutOff
from core.llm.effort_policy import EFFORTS, CALL_TIMEOUT, call_ceiling
from core.role_defaults import ORCHESTRATOR, ROLES, DEFAULT_ROLE_TABLE
from core.llm.session import Session
from core.llm.client import LiteLLMClient
from core.llm.claude import ClaudeCodeClient, find_claude, signed_in
from core.llm.scripted import ScriptedLLMClient
from core.llm.roles import RoleClient
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
    "effort_is_honoured",
    "selected_model_name",
    "client_from_settings",
    "client_from_roles",
)


def __getattr__(name):
    if name in _FACTORY_NAMES:
        from core.llm import factory
        return getattr(factory, name)
    raise AttributeError(f"module 'core.llm' has no attribute {name!r}")
