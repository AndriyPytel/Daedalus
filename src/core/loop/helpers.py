"""Helper functions for the run loop - revision tracking and termination."""
from core.events import ABORTED, BLOCKED, BUDGET_EXHAUSTED, STOPPED, TOOL_RESULT
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


def _rollback_of(planned):
    """
    The rollback a Plan's reply carried, as `(to_step, reason)`, or `None`
    where the reply carries no `rollback` field -- every reply but the one
    that undoes something.
    """
    if not planned.rollback:
        return None
    return planned.rollback["to"], planned.rollback["why"]


def _mark_rolled_back(history, to_step, upto, reason):
    """
    Mark the standing History entries for Steps `to_step + 1`..`upto` as
    rolled back, most recently added first. A Step number can appear more
    than once in `history` when it was rebuilt after an earlier rollback;
    only its current, unmarked entry is the one this rollback undid.
    """
    left = set(range(to_step + 1, upto + 1))
    for entry in reversed(history):
        if entry["step"] in left and entry["rolled_back"] is None:
            entry["rolled_back"] = reason
            left.discard(entry["step"])
        if not left:
            break


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


_KERNEL_RESULT_NAMES = ("apply_step", "execute")


_REFUSED_STATUSES = ("refused", "failed")


def _last_kernel_result(tried, events):
    """
    The last word the kernel, or this phase's own bookkeeping, had on the
    handed-back Step -- `outcome["tried"]`'s own last attempt where
    Preflight or apply rejected one, or, where nothing was ever declared
    for it, this phase's last refusal off the event stream. A result
    carrying no `step` is a look Execute took while exploring, not a verdict
    on any Step, and is skipped rather than read as the last word. `None`
    where the last result that did name a Step was a success, or there was
    none at all, since a run-scoped record can hold an unrelated Step's old
    rejection and say nothing about this one.
    """
    if tried:
        return tried[-1].get("traceback") or None
    for event in reversed(list(events)):
        if event.kind != TOOL_RESULT or event.data.get("name") not in _KERNEL_RESULT_NAMES:
            continue
        result = event.data.get("result") or {}
        if not isinstance(result, dict) or "step" not in result:
            continue
        if result.get("status") in _REFUSED_STATUSES or result.get("ok") is False:
            return result.get("traceback") or None
        return None
    return None


def _going_nowhere(handback, kernel_result):
    """
    The `going_nowhere` block, closed with what this phase's kernel
    actually said, not a guess about the description.

    A kernel result on record means the kernel, not the description, had
    the last word on this Step, so the block quotes that refusal instead
    of sending the user to fix a description that was never in question.
    Nothing on record leaves the hand-back's own account as the only word,
    so the usual advice to correct the description stands.
    """
    closing = ("The kernel's last word on this Step:\n" + kernel_result if kernel_result
              else _CORRECT_THE_DESCRIPTION)
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

