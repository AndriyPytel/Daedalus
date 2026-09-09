"""Effort policy constants and profile mapping."""

EFFORTS = ("low", "medium", "high")

PROFILES = ("thinking", "doing")

_PHASES_OF_PROFILE = {
    "thinking": ("design", "plan"),
    "doing": ("execute", "assertion", "review"),
}

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


def profile_of(phase: str) -> str:
    """
    Which of the two profiles answers *phase*.

    `thinking` is Design and Plan: reading a drawing and turning it into a
    Step list is the demand the higher end of the dial buys headroom for.
    `doing` is Execute, Assertion and Review, which already have the Step in
    hand -- paying Design's rate on every one of them is one dial's worth of
    the wrong bill. A phase this map has never heard of is `doing`, the
    cheaper of the two guesses.
    """
    for profile, phases in _PHASES_OF_PROFILE.items():
        if phase in phases:
            return profile
    return "doing"
