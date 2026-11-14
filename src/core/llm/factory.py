"""The LLM client for a run: the one model and one Effort the chat names, with the key and address its provider is stored under."""
import uuid

from core import model_catalog, settings
from core.llm.effort_policy import EFFORTS, _SUBSCRIPTION_EFFORT
from core.llm.attachments import litellm_model_name

_GATEWAY_SESSION = {"OpenCode Go": "fcc-%s" % uuid.uuid4().hex}


def effort() -> str:
    """
    The Effort, as the user last set it.

    Falls back rather than trusting what is stored: a level the settings hold
    from some older build is a flag no route can spell, and the CLI answers a
    bad `--effort` by refusing the call.
    """
    chosen = settings.instance().get_effort()
    return chosen if chosen in EFFORTS else _SUBSCRIPTION_EFFORT


def _gateway_headers(provider: str) -> dict | None:
    """The header a gateway routes on, or None where the provider needs none."""
    session = _GATEWAY_SESSION.get(provider)
    return {"x-opencode-session": session} if session else None


def client_for(provider: str, model: str, level: str):
    """
    The client for *model* at *provider*, asked to think at *level*.

    The subscription has a client of its own, since LiteLLM has no route to it, and OpenCode Go answers a call without one `x-opencode-session` header for the whole process with 400.
    A model served at an address is capped at the output ceiling its provider lists, since LiteLLM has none for it.
    """
    from core.llm.client import LiteLLMClient
    from core.llm.claude import ClaudeCodeClient

    sm = settings.instance()
    secret = sm.get_api_key_for_provider(provider)
    address = sm.get_base_url_for_provider(provider)
    if not model_catalog.configured(provider, secret, address):
        raise ValueError(
            f"'{provider}' has no key or address. Add one in Preferences → API & Providers.")

    if model_catalog.needs_no_key(provider):
        return ClaudeCodeClient(model=model, effort=level)

    api_base = model_catalog.api_base_for(provider, address)
    ceiling = (model_catalog.output_ceiling(provider, model, secret, address or None)
               if api_base else None)
    return LiteLLMClient(
        model=litellm_model_name(provider, model),
        api_key=secret or ("not-needed" if api_base else ""),
        api_base=api_base,
        thinking_budget=2048 if provider == "Anthropic" else None,
        effort=level,
        extra_headers=_gateway_headers(provider),
        output_ceiling=ceiling,
    )


def client_from_settings():
    """The client the chat's model and Effort name, or a `ValueError` saying why there is none."""
    provider, model = settings.instance().get_last_model()
    if not model:
        raise ValueError("No model selected. Configure API keys in Preferences.")
    return client_for(provider, model, effort())
