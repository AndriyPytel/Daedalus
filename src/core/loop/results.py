"""Result types: LoopResult, Progress, Stopped, _OutOfRevisions."""
from dataclasses import dataclass, field
from typing import List



@dataclass
class LoopResult:
    """
    How a run ended, and what it leaves behind: `verdict` is `None` where
    Review never ran, `"fail"` where it ran and rejected the result, and
    `"pass"` where it passed or the user accepted one that did not.

    `unmet` is what the acceptance checks said did not hold when the run
    ended: `ok=True` beside a non-empty `unmet` means taken as it is, not
    passed, and the next run inherits it since a saved Part carries nothing
    else saying so. `the_part_path` is the Part this run planned from, empty
    where neither writing nor reading one happened; an inheriting run with
    no new attachment reads the same Part rather than describing it again.
    """

    ok: bool = False
    blocked: bool = False
    verdict: str = None
    reason: str = ""
    steps_applied: int = 0
    tried: List[dict] = field(default_factory=list)
    unmet: List[str] = field(default_factory=list)
    the_part_path: str = ""
    disputed: List[dict] = field(default_factory=list)

    @property
    def awaiting_user(self) -> bool:
        """Blocked means stopped and waiting, not failed and finished."""
        return self.blocked


class Stopped(Exception):
    """The user stopped or aborted the run. Not a failure — a decision."""


class _OutOfRevisions(Exception):
    """
    The shared revision ceiling is spent. Raised where the rewrite was asked
    for, because that is the only place that knows which cause asked and what
    had been tried by then; carries the finished reason so the block does not
    have to work it out again.
    """

    def __init__(self, reason, tried):
        super().__init__(reason)
        self.reason = reason
        self.tried = tried


@dataclass
class Progress:
    """
    What a run has done to the Document so far, carried across every Plan and
    every revision within it — the one state a run's `except` clauses and its
    final `LoopResult` both read from, so it is built once in `run` and handed
    down rather than kept as several locals.

    Named fields catch a typo'd read or write as `AttributeError` at the call
    site instead of a dict silently growing a new, never-read key.
    """
    applied: int = 0
    tried: list = field(default_factory=list)
    unmet: list = field(default_factory=list)
    satisfied: list = field(default_factory=list)
    the_part_path: str = ""
