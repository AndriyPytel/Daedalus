"""Helper functions for the run loop - revision tracking and termination."""
from core.events import ABORTED, BLOCKED, BUDGET_EXHAUSTED, OUTSTANDING, STOPPED
from core.loop.describe import _INVENTED
from core.loop.results import LoopResult


def _assumed(steps):
    """
    The prose of every Step that states a value nobody supplied.

    Compared as text and whole, rather than by the number inside it: what the
    Gate shows the user is the sentence, and a sentence they have not read is
    one they cannot correct — whether it changed the radius, the shape it is a
    radius of, or the datum it is measured from.

    Found by the word `_INVENTED` names, because Plan is told to write every
    value it chose into the prose that uses it — "assumed module 2" — so the
    word is where an invented number is visible, and it is the only place it is
    visible before the geometry.
    """
    return {" ".join(str(step).split()) for step in steps
            if _INVENTED in str(step).lower()}


def _still_wrong(outstanding):
    """What the first Plan of an inheriting run is told, before it plans anything."""
    return (
        "This Document was left by an earlier run whose acceptance checks did not pass, "
        "and nobody fixed what they found. These entries were wrong then and are wrong "
        "now:\n"
        + "\n".join("  - " + entry for entry in outstanding)
        + "\nPlan the part the drawing asks for, and put right what is listed above as "
          "part of it. Do not plan around it and do not assume the existing features are "
          "correct where this list says they are not."
    )


def _carried_over(previous, steps, applied):
    """
    How many Steps at the head of a revised Plan are already standing in the
    Document, and so must not be written or applied again.

    `_run` keeps the Steps of the pass it just worked through and how many of
    them landed. A revision is a new Plan (`_run`'s own docstring), but where
    it opens with the same prose as Steps that already applied, those Steps
    describe nothing Execute has not already built — asking for their code
    again is asking for the same Document twice. Matched to a recorded run
    (`logs/sessions/0de34f88d1274dcebb02ac67e0e52fb0.jsonl`): an 11-Step Plan
    revised three times produced 28 `step_started` events over what should
    have been 11, because every revision restarted Execute at Step 1 no
    matter how much of the Plan it left untouched.

    The count is the longest run of identical prose from the head of both
    Plans, capped at how many Steps of `previous` actually landed: a Step
    that was never reached is not carried just because a revision kept its
    words, and a Step a revision changed is never carried past where it
    changed.
    """
    if previous is None:
        return 0
    carried = 0
    for was, now in zip(previous[:applied], steps):
        if was != now:
            break
        carried += 1
    return carried


def _also_check(acceptance, outstanding):
    """
    The acceptance list, with anything an earlier run left wrong added to it.

    A second drawing describes the part it wants and says nothing about what
    the run before it got wrong, so its acceptance list cannot see the six
    entries that were already failing. Adding them is what makes the checks at
    the end of this run an answer about the whole Document rather than about
    the changes it happened to make.
    """
    listed = list(acceptance)
    known = set(listed)
    return listed + [entry for entry in outstanding if entry not in known]


def _trailing_gap(written, number, total):
    """
    Whether Step `number`'s missing code leaves nothing coded after it either,
    up to `total` — a trailing gap, the reply running out of room before it
    got there — as opposed to a Step after `number` landing code in the same
    reply, an interior gap where the model wrote past a Step it skipped.

    Only meaningful where Step `number` itself has no code; the last Step of
    the range has nothing after it to check and is always a trailing gap.
    """
    return not any(written.for_step(later).code for later in range(number + 1, total + 1))



def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


def _block(events, reason, tried, applied, said=""):
    events.emit(BLOCKED, reason=reason, tried=list(tried), provider_said=said)
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)


def _stopped(events, reason, applied):
    """
    A user `Stopped` this run, not a `blocked` one: the loop's own
    `blocked`/`awaiting_user` fields still hold — a stopped run is stopped
    and waiting, not failed and finished — but the event stream says which
    of the two happened, so `core/measure.py`'s blocked rate counts only
    the runs that actually ran out of road.
    """
    events.emit(STOPPED, reason=reason)
    return LoopResult(ok=False, blocked=True, reason=reason, steps_applied=applied)


def _aborted(events, reason, applied):
    """
    A user `Aborted` this run -- the same `Stopped` exception as a
    plain Stop, told apart at `run`'s own boundary by the `aborted` callable
    it was handed.

    `blocked`/`awaiting_user` hold for the same reason `_stopped`'s do: an
    aborted run is stopped and waiting, not failed and finished, and the
    event stream is what says which of `stopped`, `aborted` and `blocked`
    actually happened here.
    """
    events.emit(ABORTED, reason=reason)
    return LoopResult(ok=False, blocked=True, reason=reason, steps_applied=applied)


def _budget_exhausted(events, reason, tried, applied):
    """
    `_OutOfRevisions` specifically -- the run did not hit a genuine
    impasse, it spent its own ceiling of attempts and revisions. Kept off
    `blocked` so `core/measure.py`'s blocked rate reads only the runs that ran
    out of road for a reason that was not the budget itself.
    """
    events.emit(BUDGET_EXHAUSTED, reason=reason, tried=list(tried))
    return LoopResult(ok=False, blocked=True, reason=reason, tried=list(tried),
                      steps_applied=applied)

