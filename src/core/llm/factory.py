"""Factory functions that build clients from settings."""
import sys

from core import model_catalog, settings
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

    The subscription route always honours it -- `--effort` is the CLI's own
    flag -- and so does Anthropic on a key, where the dial moves the thinking
    budget. Anywhere else it is LiteLLM's table that knows, and the table
    lives behind an import that costs seconds: the panel asks this while it is
    being built, so the question is only put to LiteLLM when something else
    has already paid to load it. Unknown means yes, the same benefit of the
    doubt `_may_reason` gives a gateway's own model.
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


def client_from_settings(model_id: str = None, effort: str = None):
    """
    Build a client from the provider settings page and stored keys.

    A local server's address is a setting of its own rather than the key field,
    and a gateway's is fixed and the catalog knows it. LiteLLM wants a key even
    where the local server ignores it — and has no route at all to a
    subscription, which is why one provider is answered by a client of its own
    before any of that is read.

    *effort* is the level the role's row names; left `None` the client falls
    back to the single stored dial.
    """
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
    )


def client_from_roles():
    """
    Build `RoleClient` from the settings table: one client per row, keyed by role.

    A row naming a model no configured provider reports is dropped, not
    raised: the table is seeded with defaults nobody chose, and the role then
    answers from the caller's own model, as a missing row already does.
    """
    from core.llm.roles import RoleClient

    sm = settings.instance()
    roles = {}
    for row in sm.get_role_table():
        try:
            roles[row["role"]] = client_from_settings(row["model"], row.get("effort"))
        except ValueError:
            continue
    return RoleClient(roles=roles, caller=client_from_settings())
