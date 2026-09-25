"""Helper functions for the run loop - revision tracking and termination."""
import re

from core.events import ABORTED, BLOCKED, BUDGET_EXHAUSTED, STOPPED
from core.loop.describe import _INVENTED
from core.loop.execute import _GOING_NOWHERE
from core.loop.results import LoopResult


def _assumed(steps):
    """The prose of every Step that states a value nobody supplied."""
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


def _carried_over(previous, steps, applied, wording_only=False):
    """
    How many Steps at the head of a revised Plan are already standing in the Document, and so must not be written or applied again.

    Exact match by default, the longest identical prefix capped at how many Steps of `previous` actually landed; `wording_only` relaxes that to a Step's leading word plus its numerals unchanged, for the one cause that does not call the earlier Steps into question -- Execute handing a later Step back unbuilt, where a revision only reworded what already landed. A changed numeral -- a diameter, a length, a count, a radius -- means the geometry moved even under a matching leading word, so it breaks the carried prefix the same as every other cause does, because those do call the earlier Steps into question and keep the exact match.
    """
    if previous is None:
        return 0
    carried = 0
    for was, now in zip(previous[:applied], steps):
        same = _reworded(was, now) if wording_only else was == now
        if not same:
            break
        carried += 1
    return carried


def _reworded(was, now):
    """A Step is the same build reworded when its op verb and its numerals both hold."""
    return _leading_word(was) == _leading_word(now) and _numerals(was) == _numerals(now)


def _leading_word(step):
    """The first word of a Step's prose, lowered -- the op verb every Step opens with."""
    said = str(step).strip().split(None, 1)
    return said[0].lower() if said else ""


def _numerals(step):
    """Every number a Step's prose names, in order -- a diameter, a length, a count, a radius."""
    return re.findall(r"\d+(?:\.\d+)?", str(step))


def _also_check(acceptance, outstanding):
    """
    The acceptance list, with anything an earlier run left wrong added to it.

    A second drawing says nothing about what the run before it got wrong, so
    its own acceptance list cannot see those entries. Adding them makes the
    checks answer for the whole Document, not just the changes made now.
    """
    listed = list(acceptance)
    known = set(listed)
    return listed + [entry for entry in outstanding if entry not in known]


def _trailing_gap(written, number, total):
    """
    Whether Step `number`'s missing code leaves nothing coded after it
    either, up to `total` — a trailing gap, the reply running out of room —
    as opposed to a later Step landing code, an interior gap where the model
    wrote past a Step it skipped. Only meaningful where `number` has no code.
    """
    return not any(written.for_step(later).code for later in range(number + 1, total + 1))



def _tried(step, attempt):
    return {"step": step, "code": attempt.code, "traceback": attempt.traceback}


_CORRECT_THE_DESCRIPTION = (
    "Correct the description — the values it holds in conflict, and the datums and "
    "overalls a superseded feature left behind — and run it again."
)


def _going_nowhere(handback, ledger):
    """
    The `going_nowhere` block, closed with what the run actually knows.

    A ledger that already holds an entry means the kernel, not the
    description, had the last word on this Step, so the block quotes that
    refusal instead of sending the user to fix a description that was
    never in question. An empty ledger leaves the hand-back's own account
    as the only word on it, so the usual advice to correct the description
    stands.
    """
    closing = "The kernel's last word on this Step:\n" + ledger.render() if ledger \
        else _CORRECT_THE_DESCRIPTION
    return _GOING_NOWHERE % (handback, closing)


def _block(events, reason, tried, applied, said="", verdict=None):
    events.emit(BLOCKED, reason=reason, tried=list(tried), provider_said=said)
    return LoopResult(ok=False, blocked=True, verdict=verdict, reason=reason,
                      tried=list(tried), steps_applied=applied)


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
    A user `Aborted` this run -- the same `Stopped` exception as a plain
    Stop, told apart at `run`'s own boundary by the `aborted` callable it
    was handed. `blocked`/`awaiting_user` hold for the reason `_stopped`'s
    do: stopped and waiting, not failed and finished, and the event stream
    says which of `stopped`, `aborted` and `blocked` happened.
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

