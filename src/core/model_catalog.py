"""
The models a key can actually call, asked of the provider itself, because a
bundled model list was a second answer to the same question that could only
go stale. Owns the whole path -- URL, headers, request, parsing -- so the
endpoint and its auth sit inside the tested seam, and is FreeCAD-free so it
is importable in tests. Uses httpx rather than vendor SDKs, since only
litellm is a dependency of this addon and httpx ships with it, carrying
certifi's trust store.
"""
import httpx

TIMEOUT = 10.0

CLAUDE = "Claude (subscription)"

_DEFAULT_BASE = {
    "OpenAI": "https://api.openai.com/v1",
    "Anthropic": "https://api.anthropic.com/v1",
    CLAUDE: "",
    "Google": "https://generativelanguage.googleapis.com/v1beta",
    "OpenCode Go": "https://opencode.ai/zen/go/v1",
    "OpenRouter": "https://openrouter.ai/api/v1",
    "Custom/Local": "",
}

_VENDORS = ("OpenAI", "Anthropic", "Google", "OpenRouter")

_ALIASES = ("opus", "sonnet", "haiku")

MODELS_DEV = "https://models.dev"


def providers() -> list[str]:
    """Every provider the Preferences page offers, in the order it offers them."""
    return list(_DEFAULT_BASE)


def needs_base_url(provider: str) -> bool:
    """True for a provider whose address only the user knows — a local server."""
    return not needs_no_key(provider) and not _DEFAULT_BASE.get(provider)


def needs_no_key(provider: str) -> bool:
    """
    True for the provider that holds its own credential.

    The `claude` CLI is signed in to a subscription, so there is neither a key
    to store nor an address to type. Everywhere else in the addon "no key and
    no address" means "not configured yet", which would leave this provider out
    of the model list, out of `get_llm_credentials` and out of every fetch.
    """
    return provider == CLAUDE


def api_base_for(provider: str, base_url: str = "") -> str | None:
    """
    The address LiteLLM must be told, or None when it knows it already.

    A gateway speaks the OpenAI shape at an address of its own; naming one to
    LiteLLM as `openai/...` without it would send the request to OpenAI.
    """
    if provider in _VENDORS:
        return None
    return openai_base(base_url or _DEFAULT_BASE.get(provider, "")) or None


def openai_base(base_url: str) -> str:
    """
    A server address, as the OpenAI-compatible root LiteLLM and ``fetch`` want.

    What a user types is where the server listens — `http://localhost:11434`.
    Ollama, llama.cpp and LM Studio all serve the OpenAI shape under `/v1`, so
    appending it is the addon's business rather than the user's to remember;
    an address that already says `/v1` is left alone.
    """
    base = (base_url or "").strip().rstrip("/")
    if base and not base.endswith("/v1"):
        base += "/v1"
    return base


_NOT_A_READER = ("embed", "tts", "whisper", "image", "audio", "moderation",
                 "rerank", "sora", "video")


def _offerable(provider: str, model_id: str, accepts_images=None) -> bool:
    """
    Whether *model_id* is worth offering: the addon's work is a drawing in
    and code out, so a model that cannot see one is no use for it, narrower
    than dropping embeddings since `o3-mini` is a chat model with no eyes.
    Only the three vendors LiteLLM names itself are judged, because a
    gateway sells under ids of its own and a local server serves whatever
    was pulled, so LiteLLM's silence about those is not a verdict. LiteLLM's
    own table is always a release behind the model everyone wants, so an id
    it has never heard of is offered rather than hidden, except the words in
    ``_NOT_A_READER``, since nothing called an embedding or a voice was ever
    going to read a drawing.
    """
    if provider not in _VENDORS:
        return True

    from core.llm import litellm_model_name
    if accepts_images is None:
        from core.llm import accepts_images

    verdict = accepts_images(litellm_model_name(provider, model_id))
    if verdict is None:
        return not any(word in model_id.lower() for word in _NOT_A_READER)
    return bool(verdict)


def fetch(provider: str, api_key: str, base_url: str | None = None,
          accepts_images=None) -> list[dict]:
    """
    Return ``[{id, display}]`` for ``provider``, raising on transport or HTTP
    failure; ``base_url`` overrides the provider's endpoint, for the
    Custom/Local server or a test. A local server's address is a setting of
    its own, never the API key, since a key read as a URL breaks the
    request; its key stays optional, ignored by a server that wants none.
    The subscription answers out of this module rather than over a socket,
    since its CLI validates a model but cannot enumerate one, and its three
    aliases are the only bundled list that cannot go stale.
    """
    if needs_no_key(provider):
        return _subscription_models(base_url or MODELS_DEV)

    base = (base_url or _DEFAULT_BASE.get(provider, "")).rstrip("/")
    params = {}

    if provider in ("OpenAI", "OpenCode Go", "OpenRouter"):
        headers = {"Authorization": "Bearer %s" % api_key}
    elif provider == "Anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        params = {"limit": 1000}
    elif provider == "Google":
        headers = {}
        params = {"key": api_key, "pageSize": 1000}
    else:
        if not base_url:
            raise ValueError("No Base URL for provider: %s" % provider)
        base = openai_base(base_url)
        headers = {"Authorization": "Bearer %s" % api_key} if api_key else {}

    if not base:
        raise ValueError("No endpoint for provider: %s" % provider)

    response = httpx.get(base + "/models", headers=headers, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if provider == "Google":
        found = [
            {"id": entry["name"].removeprefix("models/"),
             "display": entry.get("displayName") or entry["name"].removeprefix("models/")}
            for entry in payload.get("models", [])
            if "generateContent" in entry.get("supportedGenerationMethods", [])
        ]
    else:
        found = [
            {"id": entry["id"], "display": entry.get("display_name") or entry["id"]}
            for entry in payload.get("data", [])
        ]

    return [model for model in found
            if _offerable(provider, model["id"], accepts_images)]


def _subscription_models(base: str) -> list[dict]:
    """
    The CLI's aliases, then every Anthropic id models.dev lists, so two
    versions behind one alias can be picked apart. `/v1/models` refuses a
    request with no API key, and models.dev answers one; when it is down the
    aliases still work, so only the versions are lost.
    """
    aliases = [{"id": alias, "display": alias} for alias in _ALIASES]
    try:
        response = httpx.get(base.rstrip("/") + "/api.json", timeout=TIMEOUT)
        response.raise_for_status()
        listed = response.json().get("anthropic", {}).get("models", {})
    except (httpx.HTTPError, ValueError):
        return aliases
    return aliases + [{"id": model_id, "display": entry.get("name") or model_id}
                      for model_id, entry in listed.items()]
