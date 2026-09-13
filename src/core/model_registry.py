"""
Where a model id last resolved to a provider, kept outside
`bridge.provider_manager`'s Qt-signal cache so `core` can read it without
reaching back into the Qt-touching layer. `core.settings.get_llm_credentials`
needs this once a model has been chosen but no live fetch is running to name
it again; `provider_manager` still owns the actual fetch and records
everything it learns here too, so a model that resolved once keeps its
provider even after a key change clears the fetch that named it.
"""
_resolved = {}


def remember(model_id: str, provider: str, display: str):
    _resolved[model_id] = (provider, display)


def get_model_entry(model_id: str) -> dict | None:
    """The last-known ``{id, display, provider}`` for *model_id*, or `None`."""
    remembered = _resolved.get(model_id)
    if remembered is None:
        return None
    provider, display = remembered
    return {"id": model_id, "display": display, "provider": provider}
