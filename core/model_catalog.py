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
    elif provider == "Custom/Local":
        # The Custom/Local page stores the endpoint where others store a key.
        base = (base_url or api_key).rstrip("/")
        headers = {}
    else:
        raise ValueError("Unknown provider: %s" % provider)

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
