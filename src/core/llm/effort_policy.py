"""Effort policy constants."""

EFFORTS = ("low", "medium", "high", "xhigh", "max")

_OUTPUT_BUDGET = 16384

_REASONING_EFFORT = "low"

_SUBSCRIPTION_EFFORT = "medium"

_CEILING = {"low": 300.0, "medium": 900.0, "high": 1500.0, "xhigh": 2400.0, "max": 3600.0}

STALL_LIMIT = 300.0

STALL_WAIT = 600.0

CALL_TIMEOUT = _CEILING[_SUBSCRIPTION_EFFORT]


def call_ceiling(effort: str) -> float:
    """The seconds one *effort* call may take before it is cut off."""
    return _CEILING[effort]

_THINKING_BUDGET = {"low": 1024, "medium": 2048, "high": 4096}


def _resolved_effort(explicit: str = None) -> str:
    """*explicit* when it is one `EFFORTS` offers, an error when it is anything else; the stored dial when there is none."""
    if explicit:
        if explicit not in EFFORTS:
            raise ValueError("effort must be one of %s, not %r" % (", ".join(EFFORTS), explicit))
        return explicit
    from core.llm.factory import effort
    return effort()

