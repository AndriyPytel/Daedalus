"""
The models a key can actually call, asked of the provider itself. The table of
providers is the registry of what the addon can reach; a bundled model list was
a second answer to the same question, and one that could only go stale.

Owns the whole path — URL, headers, request, parsing — so the endpoint and its
auth sit inside the tested seam. FreeCAD-free, so it is importable in tests.

httpx, not vendor SDKs: they are not dependencies of this addon — only litellm
is — and httpx ships with litellm carrying certifi's trust store.
"""
import httpx

TIMEOUT = 10.0

_DEFAULT_BASE = {
    "OpenAI": "https://api.openai.com/v1",
    "Anthropic": "https://api.anthropic.com/v1",
    "Google": "https://generativelanguage.googleapis.com/v1beta",
    "OpenCode Go": "https://opencode.ai/zen/go/v1",
    "Custom/Local": "",
}

_VENDORS = ("OpenAI", "Anthropic", "Google")


def providers() -> list[str]:
    """Every provider the Preferences page offers, in the order it offers them."""
    return list(_DEFAULT_BASE)


def needs_base_url(provider: str) -> bool:
    """True for a provider whose address only the user knows — a local server."""
    return not _DEFAULT_BASE.get(provider)


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
    Whether *model_id* is worth offering: the addon's work is a drawing in and
    code out, so a model that cannot see one is no use for it. Narrower than
    dropping embeddings — `o3-mini` is a chat model with no eyes.

    Only the three vendors LiteLLM names itself are judged. A gateway sells
    under ids of its own and a local server serves whatever was pulled, so
    LiteLLM's silence about those is not a verdict.

    LiteLLM's table ships with LiteLLM and so is always a release behind the
    model everyone wants, which is why an id it has never heard of is offered
    rather than hidden. The words in ``_NOT_A_READER`` are the exception:
    nothing called an embedding or a voice was ever going to read a drawing.
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
    failure. ``base_url`` overrides the provider's endpoint — it is where the
    Custom/Local server lives, and where tests point.

    ``accepts_images`` is the sight verdict, ``llm.accepts_images`` by default:
    True, False, or None for a model LiteLLM has never heard of.

    A local server's address is a setting of its own, never the API key — a key
    read as a URL is what produced "Request URL is missing an 'http://' or
    'https://' protocol". Its key is optional: a server that wants no token
    ignores the header, one that does gets it, which is why the key field
    stays. Anthropic and Google both page their model lists, so each is asked
    for one page of 1000, which nobody is near.
    """
    base = (base_url or _DEFAULT_BASE.get(provider, "")).rstrip("/")
    params = {}

    if provider in ("OpenAI", "OpenCode Go"):
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
