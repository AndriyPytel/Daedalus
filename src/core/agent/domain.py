"""
A Domain is what one loop works under: a system prompt, the first message, the Assertion asked once at the first accepted `finish`, and the done-check that refuses every `finish` until the Document is what the Domain asks for.
The Part Domain is the reader's wiring, the Assembly Domain is `core.agent.assembly` and the BIM Domain is `core.agent.bim`; `PART` is the default wherever a caller names none.
"""
from dataclasses import dataclass, field
from typing import Callable

PART = "part"
ASSEMBLY = "assembly"
BIM = "bim"


def label(domain):
    """*domain* as the user reads it: `BIM` in capitals, the rest capitalised."""
    return domain.upper() if domain == BIM else domain.capitalize()


@dataclass(frozen=True)
class Domain:
    """*assertion* answers with the content to send back and the names of the views it draws, or `None`; *done_check* answers with why the Document is not finished, or `None`; *calls* maps a reply's key to the handler that answers it with a dict."""

    name: str
    system: str
    opening: list
    assertion: Callable
    done_check: Callable
    calls: dict = field(default_factory=dict)


def unfinished(kernel, check, refusals, name, unmeasurable):
    """Why the Document is not finished under *name*'s Assertion, one line per failing condition of *refusals* over the facts *check* measures, or `None` where it holds; *unmeasurable* is said where the facts could not be had."""
    facts = kernel.measure(check)
    if facts is None:
        return unmeasurable
    found = refusals(facts)
    return ("The %s Assertion does not hold:\n- " % name + "\n- ".join(found)
            + "\nFix this, then finish.") if found else None
