"""The result of an agent run."""
from dataclasses import dataclass


@dataclass
class LoopResult:
    """How a run ended: `ok` where something landed and the Domain's done-check holds, else `reason` says why not."""

    ok: bool = False
    reason: str = ""
    steps_applied: int = 0
