"""Result types: LoopResult, Progress, Stopped, _OutOfRevisions."""
from dataclasses import dataclass, field
from typing import List



@dataclass
class LoopResult:
    """
    How a run ended, and what it leaves behind.

    `unmet` is what the acceptance checks said did not hold when the run ended,
    whatever the run ended as. A run the user took as it is still leaves these
    wrong in the Document, and the next run is entitled to know -- `ok=True`
    beside a non-empty `unmet` is exactly that: taken as it is, not passed. A
    saved Part carries nothing else saying so (logs/analysis-mm2023-defects.md:
    a failing pocket and lug, unrecorded anywhere), which is why both of
    `_run`'s ACCEPT returns set `unmet` from the same acceptance list every
    other ending already reads.

    `the_part_path` is the Part reading this run planned from: the one it
    wrote, or the one it was handed and skipped Design to read instead
    (FCC-210). Empty where neither happened. A run that inherits it and gets
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

    @property
    def awaiting_user(self) -> bool:
        """Blocked means stopped and waiting, not failed and finished."""
        return self.blocked


class Stopped(Exception):
    """
    The user stopped or aborted the run. Not a failure — a decision.

    One exception for both: every `cancelled()` check in this module already
    raises it the instant the user's flag reads true, wherever that happens to
    be — mid-attempt, mid-Step, mid-revision. What tells stop and abort apart
    is not where this was raised but how the in-flight Step got here. A Stop
    lets the kernel finish the Step it was already running and checkpoint it,
    so `cancelled()` only turns true between attempts; an Abort kills the
    kernel while a Step is still running, so the same check() call that would
    have returned success instead comes back a crash and unwinds here anyway.
    `run`'s own boundary is where the two are told apart and where the event
    is stamped `stopped` or `aborted` — the `aborted` callable it is handed
    answers only that one question.
    """


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
    site instead of a dict silently growing a new, never-read key (FCC-276).
    """
    applied: int = 0
    tried: list = field(default_factory=list)
    unmet: list = field(default_factory=list)
    satisfied: list = field(default_factory=list)
    the_part_path: str = ""
