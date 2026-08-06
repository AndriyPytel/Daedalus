# FreeCadCopilot/core/model_catalog.py
"""
The models a key can actually call, asked of the provider itself.

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
}


def needs_base_url(provider: str) -> bool:
    """True for a provider whose address only the user knows — a local server."""
    return provider not in _DEFAULT_BASE


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


def fetch(provider: str, api_key: str, base_url: str | None = None) -> list[dict]:
    """
    Return ``[{id, display}]`` for ``provider``, raising on transport or HTTP
    failure. ``base_url`` overrides the provider's endpoint — it is where the
    Custom/Local server lives, and where tests point.
    """
    base = (base_url or _DEFAULT_BASE.get(provider, "")).rstrip("/")
    params = {}

    if provider == "OpenAI":
        headers = {"Authorization": "Bearer %s" % api_key}
    elif provider == "Anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        # ponytail: one page of 1000 — both providers page, and nobody is near it.
        params = {"limit": 1000}
    elif provider == "Google":
        headers = {}
        params = {"key": api_key, "pageSize": 1000}
    else:
        # A local server. Its address is a setting of its own (FCC-50), never
        # the API key — a key read as a URL is what produced "Request URL is
        # missing an 'http://' or 'https://' protocol".
        if not base_url:
            raise ValueError("No Base URL for provider: %s" % provider)
        base = openai_base(base_url)
        # A local server that wants no token ignores the header; one that does
        # gets it, which is why the key field stays.
        headers = {"Authorization": "Bearer %s" % api_key} if api_key else {}

    if not base:
        raise ValueError("No endpoint for provider: %s" % provider)

    response = httpx.get(base + "/models", headers=headers, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()

    if provider == "Google":
        return [
            {"id": entry["name"].removeprefix("models/"),
             "display": entry.get("displayName") or entry["name"].removeprefix("models/")}
            for entry in payload.get("models", [])
            if "generateContent" in entry.get("supportedGenerationMethods", [])
        ]

    return [
        {"id": entry["id"], "display": entry.get("display_name") or entry["id"]}
        for entry in payload.get("data", [])
    ]
