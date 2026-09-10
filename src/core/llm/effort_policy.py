"""Effort policy constants."""

EFFORTS = ("low", "medium", "high")

_OUTPUT_BUDGET = 8192

_REASONING_EFFORT = "low"

_SUBSCRIPTION_EFFORT = "medium"

_CEILING = {"low": 300.0, "medium": 900.0, "high": 1500.0}

CALL_TIMEOUT = _CEILING[_SUBSCRIPTION_EFFORT]

_THINKING_BUDGET = {"low": 1024, "medium": 2048, "high": 4096}


def _resolved_effort(explicit: str = None) -> str:
    """*explicit* when it is one `EFFORTS` offers; the stored dial otherwise."""
    if explicit in EFFORTS:
        return explicit
    from core.llm.factory import effort
    return effort()

