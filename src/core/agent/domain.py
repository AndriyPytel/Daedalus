"""
A Domain is what one loop builds under: a system prompt, the first message, the Assertion asked once at the first accepted `finish`, and the done-check that refuses every `finish` until the Document is what the Domain builds.
The Part Domain is the reader's wiring and the Assembly Domain is `core.agent.assembly`; `PART` is the default wherever a caller names none.
"""
from dataclasses import dataclass, field
from typing import Callable

PART = "part"
ASSEMBLY = "assembly"


@dataclass(frozen=True)
class Domain:
    """*assertion* answers with the content to send back and the names of the views it draws, or `None`; *done_check* answers with why the Document is not finished, or `None`; *calls* maps a reply's key to the handler that answers it with a dict."""

    name: str
    system: str
    opening: list
    assertion: Callable
    done_check: Callable
    calls: dict = field(default_factory=dict)
