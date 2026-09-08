"""
The LLM client seam.

One interface, three implementations: LiteLLM against a keyed provider, the
`claude` CLI against a Claude Max subscription, and a scripted client that
replays canned turns. Everything above this line is testable without a network
or an API key.

ADR-0004 — LiteLLM is the single unified client. Its own spike is the
evidence that it carries Anthropic thinking blocks through the stream and
honours `cache_control` — on the `anthropic/` route, which is what
`cache_control_supported` names, since the spike never exercised the others.

ADR-0025 amends it in part. `ClaudeCodeClient` answers a phase on a
subscription the user already pays for, as a subprocess and not a package —
nothing is added to FreeCAD's shared interpreter, where the Agent SDK would add
one that wraps the same CLI. It shows no reasoning, and that silence is scoped
to the runs a user chooses to spend a subscription on rather than imposed on
every provider: Anthropic through a key keeps its thinking stream.

This module must stay importable without FreeCAD; the settings lookup that
needs it is deferred into `client_from_settings`.

A reasoning model left on the provider's default cap spends it all on thought
and answers with nothing, so `_OUTPUT_BUDGET` sets one; read it like the
Anthropic thinking budget, because the request may cost four times it. Told
nothing about effort, kimi-k3 circled one drawing for ten minutes and never
left its own reasoning, and "medium" bought no less of it — the cap turns that
into an error, and the effort dial is the only thing there is against reaching
it. `EFFORTS` is that dial, offered in the composer and stored in settings, and
both routes are told what it says: `--effort` on the subscription route, and
`reasoning_effort` or an Anthropic thinking budget on a key. Its default is
`medium`: on one sheet through Design `high` spent 41,704 tokens of thinking
against `medium`'s 26,117 and four minutes more of the wait, and Design is one
phase of a run that has several.

The CLI takes two levels past `high`, and neither is offered. Nothing here has
measured them, and `_CEILING` — the timeout that has to move with the dial or
it cuts the answer off — is derived from measurements of the three that are.
An unmeasured level would be a ceiling made up, which is the failure that
ceiling exists to prevent.

One dial over five phases is one compromise: Design reading a drawing and
Execute writing the code for a Step it has already been given are not the
same demand, and raising the dial for the first pays for it on every call of
the second. `profile_of` is the two-way split — `thinking` for Design and
Plan, `doing` for Execute, Assertion and Review — and `ProfiledClient` is what
`for_phase` answers with: two clients, each already built on its own model
and its own effort, so `_CEILING` follows each profile's own dial rather than
the one dial shared by both. `client_from_profiles` builds it from settings;
`client_from_settings` still answers a single model on request, which is what
a session's own name and a Level 2 test that names one model both still ask
for.

`IMAGE_SUFFIXES` and `PDF_SUFFIXES` are what the providers take, and what the
attach dialog offers. A PDF is a full attachment rather than a second-class
one: drawings arrive as PDFs.
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
