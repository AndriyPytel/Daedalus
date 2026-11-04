"""Factory functions that build clients from settings."""
import logging
import sys
import uuid

from core import model_catalog, settings

_LOG = logging.getLogger(__name__)
_GATEWAY_SESSION = {"OpenCode Go": "fcc-%s" % uuid.uuid4().hex}
from core.llm.effort_policy import EFFORTS, _SUBSCRIPTION_EFFORT
from core.llm.attachments import litellm_model_name


def effort() -> str:
    """
    The dial, as the user last set it.

    Falls back rather than trusting what is stored: a level the settings hold
    from some older build is a flag no route can spell, and the CLI answers a
    bad `--effort` by refusing the call.
    """
    chosen = settings.instance().get_effort()
    return chosen if chosen in EFFORTS else _SUBSCRIPTION_EFFORT


def effort_is_honoured(model_id: str = None) -> bool:
    """
    Whether asking *model_id* to think harder changes anything at all.
    The subscription route always honours it, since `--effort` is the
    CLI's own flag, and so does Anthropic on a key, where the dial moves
    the thinking budget; anywhere else it is LiteLLM's table that knows,
    asked only when something else has already paid to load it. Unknown
    means yes, the same benefit of the doubt `_may_reason` gives a
    gateway's own model.
    """
    from core.llm.client import _may_reason

    try:
        provider, mid, _ = settings.instance().get_llm_credentials(model_id)
    except Exception:
        return True
    if model_catalog.needs_no_key(provider) or provider == "Anthropic":
        return True
    if "litellm" not in sys.modules:
        return True
    return _may_reason(litellm_model_name(provider, mid))


def selected_model_name(model_id: str = None) -> str:
    """The model the panel is set to, spelled the way LiteLLM spells it."""
    provider, mid, _ = settings.instance().get_llm_credentials(model_id)
    return litellm_model_name(provider, mid)


def _gateway_headers(provider: str) -> dict | None:
    """The header a gateway routes on, or None where the provider needs none."""
    session = _GATEWAY_SESSION.get(provider)
    return {"x-opencode-session": session} if session else None


def client_from_settings(model_id: str = None, effort: str = None):
    """
    Build a client from the provider settings page and stored keys. A
    local server's address is a setting of its own rather than the key
    field, and a gateway's is fixed and the catalog knows it; LiteLLM
    wants a key even where the local server ignores it, and has no route
    at all to a subscription, which is why one provider is answered by a
    client of its own before any of that is read. *effort* is the level
    the role's row names, falling back to the single stored dial when
    `None`, and a gateway that routes on a session header gets one for the
    whole process, since OpenCode Go answers a call without
    `x-opencode-session` with 400 for every model it sells.
    """
    from core import model_registry
    from core.llm.client import LiteLLMClient
    from core.llm.claude import ClaudeCodeClient

    sm = settings.instance()
    provider, mid, secret = sm.get_llm_credentials(model_id)

    if model_catalog.needs_no_key(provider):
        return ClaudeCodeClient(model=mid, effort=effort)

    api_base = model_catalog.api_base_for(provider, sm.get_base_url_for_provider(provider))
    api_key = secret or ("not-needed" if api_base else "")

    return LiteLLMClient(
        model=litellm_model_name(provider, mid),
        api_key=api_key,
        api_base=api_base,
        thinking_budget=2048 if provider == "Anthropic" else None,
        effort=effort,
        extra_headers=_gateway_headers(provider),
        output_ceiling=model_registry.get_model_entry(mid)["max_output"],
    )


def client_from_roles():
    """
    Build `RoleClient` from the settings table: one client per row, keyed by role.

    A row naming a model no configured provider reports is dropped, not
    raised: the table is seeded with defaults nobody chose, and the role then
    answers from the caller's own model, as a missing row already does. Each
    row's outcome is logged, so which model and effort a role actually got,
    and whether that came from its own row or the caller fallback, is
    readable from the log without auditing settings by hand.
    """
    from core.llm.roles import RoleClient

    sm = settings.instance()
    caller = client_from_settings()
    roles = {}
    for row in sm.get_role_table():
        role, model, effort = row["role"], row["model"], row.get("effort")
        try:
            roles[role] = client_from_settings(model, effort)
            _LOG.info("role %s: model=%s effort=%s source=row", role, model, effort)
        except ValueError:
            _LOG.info("role %s: model=%s effort=%s source=caller-fallback",
                       role, getattr(caller, "model", None), effort)
    return RoleClient(roles=roles, caller=caller)
