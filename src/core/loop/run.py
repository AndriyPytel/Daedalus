"""The main loop runner and coordination logic."""
import os
from dataclasses import dataclass, field

from core.arithmetic import wrong_sums
from core.design import part_file
from core.design.report import distilled, render
from core.design.gaps import (
    decided_for_you,
    entries,
)
from core.events import (
    ANSWER,
    APPLIED,
    ASSERTION,
    ATTACHED,
    COMPLETED,
    ERROR,
    EventStream,
    FAILED,
    GATE,
    INTENT,
    OUTSTANDING,
    PART,
    PLAN,
    PLAN_REVISED,
    PREFLIGHT_FAILED,
    QUESTION,
    REVIEW_SUSPECTED,
    REVIEW_VERDICT,
    STEP_STARTED,
    UNBUILT,
)
from core.agent.subagent import RESERVE, SessionTreeSupervisor
from core.ledger import Ledger
from core.llm import ProviderRefused

from core.loop.assertion import (
    Checked,
    _GEOMETRY_REGRESSION,
    _geometry_regressions,
    _why_the_checks_failed,
    assertion,
)
from core.loop.budgets import Budgets, PhaseError, _Revisions, revisions_ceiling
from core.loop.describe import _DECIDED, _RAN_OUT_OF_ROOM, _describe_the_part
from core.loop.execute import (
    OP_VERBS,
    Attempt,
    Looks,
    TYPE_CACHE_PATH,
    _A_PROPERTY_OF_THE_DOCUMENT,
    _FAILED_THIS_WAY,
    _MAY_START_AGAIN,
    _SHAPES_THE_PART,
    _SKIPPED_A_STEP,
    _TURN_BUDGET_SPENT,
    _TURN_BUDGET_SPENT_REHEARSED,
    _WHAT_NOW,
    _emit_plan,
    _freecad_version,
    _review_views,
    _what_this_build_has,
    execute,
)
from core.loop.gate import (
    ACCEPT,
    APPROVE,
    Approval,
    DECISIONS,
    NeedsAnswer,
    REJECT,
    REVISE,
    START_OVER,
    auto_approve,
    nobody_to_ask,
    revise_by_default,
)
from core.loop.plan import (
    _as_written,
    _do_the_sums,
    _op_off_the_list,
    _plan_once_it_knows_enough,
    _plan_the_rest,
    _unconsumed,
)
from core.loop.results import LoopResult, Progress, Stopped, _OutOfRevisions
from core.loop.helpers import (
    _assumed, _still_wrong, _carried_over, _also_check, _trailing_gap,
    _tried, _block, _stopped, _aborted, _budget_exhausted, _going_nowhere,
    _last_kernel_result,
)
from core.loop.review import review, review_suspect


class _Asking:
    """What has been asked in this run, and of whom. Run-wide rather than per Plan."""

    def __init__(self):
        self.unanswered = []
        self.looked_up = []
        self.read_again = 0
        self.looks = 0
        self.re_aimed = 0


def run(intent, *, llm, tools, events=None, budgets=None, cancelled=None, aborted=None,
        budget_exceeded=None, attachments=(), gate=auto_approve, ask=nobody_to_ask,
        decide=revise_by_default, outstanding=(), the_part_path=None, kernel=None) -> LoopResult:
    """Turn an Intent into changes to the Document."""
    events = events if events is not None else EventStream()
    events.emit(INTENT, text=intent)
    budgets = budgets or Budgets()
    progress = Progress(unmet=list(outstanding), the_part_path=the_part_path or "")

    def outcome(result):
        """
        Stamp what is still wrong with the Document, and which Part it was
        built from, onto whatever the run returned.

        Every exit carries both, including the ones that raised: a run
        stopped halfway leaves the Document as wrong as the last check found
        it, reading from the same Part it started from — the state the next
        run inherits. Done in one place because there are nine ways out of
        `_run` and eight of them are not the happy one.
        """
        result.unmet = list(progress.unmet)
        result.the_part_path = progress.the_part_path
        if result.ok:
            events.emit(COMPLETED)
        return result

    aborted = aborted or (lambda: False)
    budget_exceeded = budget_exceeded or (lambda: False)
    try:
        return outcome(_run(intent, llm, tools, events, budgets,
                            cancelled or (lambda: False), gate, progress, attachments,
                            ask, decide, list(outstanding), the_part_path, kernel))
    except Stopped:
        if aborted():
            return outcome(_aborted(events, "Aborted. The Step in flight was discarded; "
                                            "everything applied before it is still in the "
                                            "Document.", progress.applied))
        if budget_exceeded():
            return outcome(_budget_exhausted(
                events, "An automated dollar/time ceiling stopped this run, not you. "
                        "Everything applied so far is still in the Document; undo it "
                        "Step by Step if you want it gone.", progress.tried, progress.applied))
        return outcome(_stopped(events, "Stopped by you. Everything applied so far is still in "
                                        "the Document; undo it Step by Step if you want it "
                                        "gone.", progress.applied))
    except _OutOfRevisions as spent:
        return outcome(_budget_exhausted(events, spent.reason, spent.tried, progress.applied))
    except ProviderRefused as exc:
        return outcome(_block(events, str(exc), progress.tried, progress.applied,
                              said=exc.said))
    except NeedsAnswer as exc:
        return outcome(_block(events, str(exc), progress.tried, progress.applied))
    except PhaseError as exc:
        events.emit(ERROR, message=str(exc))
        events.emit(FAILED, message=str(exc))
        return outcome(LoopResult(ok=False, reason=str(exc)))
    except part_file.MissingPyYAML as exc:
        events.emit(ERROR, message=str(exc))
        events.emit(FAILED, message=str(exc))
        return outcome(LoopResult(ok=False, reason=str(exc)))
    except Exception as exc:
        events.emit(ERROR, message="%s: %s" % (type(exc).__name__, exc))
        events.emit(FAILED, message="%s: %s" % (type(exc).__name__, exc))
        return outcome(LoopResult(ok=False, reason=str(exc)))


@dataclass
class Standing:
    """
    The Plan Gate's standing approval within one run: whether the user has
    said "yes to future Steps like these" already, and which assumed values
    they have been shown and read.
    """
    approval: bool = False
    read: set = field(default_factory=set)


@dataclass
class Part:
    """
    The description Plan and the Gate read, mutated in place by `replan` so
    every closure inside `_run` that reads it sees the latest description.
    """
    the_part: object = None


@dataclass
class LastPass:
    """
    What the previous pass over the Plan's Steps applied, and why it was
    revised, so a revised Plan knows how much of its own head is already
    standing in the Document.
    """
    steps: object = None
    applied: int = 0
    cause: str = None


def _run(intent, llm, tools, events, budgets, cancelled, gate, progress, attachments, ask,
         decide=revise_by_default, outstanding=(), the_part_path=None, kernel=None):
    """
    The run itself: describe the part, plan it, gate it, build it, judge
    it. Design runs once per run, growing what it read. A handback with
    nothing applied since the last one stops the run; any other handback
    replans on a fresh Snapshot.
    """
    if cancelled():
        raise Stopped()
    if outstanding:
        events.emit(OUTSTANDING, entries=list(outstanding))
    if attachments:
        events.emit(ATTACHED, files=[{"name": os.path.basename(path), "path": path}
                                     for path in attachments])

    if the_part_path and not attachments:
        part = Part(part_file.read(the_part_path))
    else:
        part = Part(_describe_the_part(intent, llm, events, budgets, ask, cancelled,
                                       attachments))
        written = events.last(PART)
        progress.the_part_path = written.data["path"] if written else ""

    asking = _Asking()
    ledger = Ledger(_FAILED_THIS_WAY)
    geometry_baseline = {}
    exploration = {}
    supervisor = SessionTreeSupervisor()

    def replan(feedback=None, revising=()):
        planned, part.the_part = _plan_once_it_knows_enough(
            intent, tools.call("snapshot"), llm, events, budgets, part.the_part,
            cancelled, asking, tools, feedback, attachments, revising, ledger,
            supervisor)
        return planned

    def decided(why, applied, unmet=()):
        """What the user wants done about a result that did not pass."""
        if not applied:
            return REVISE
        if auto_approve:
            events.emit(QUESTION, text=_WHAT_NOW % why, auto=True)
            events.emit(ANSWER, by="auto", text=_DECIDED[REVISE], auto=True,
                        unmet=list(unmet))
            return REVISE
        events.emit(QUESTION, text=_WHAT_NOW % why)
        answer = decide({"why": why, "applied": applied})
        answer = answer if answer in DECISIONS else REVISE
        events.emit(ANSWER, by="user", text=_DECIDED[answer])
        return answer

    def _size_ceiling(steps):
        """One ceiling for the whole run, grown to fit whichever Plan is biggest so far."""
        supervisor.turn_budget = max(supervisor.turn_budget, 3 * len(steps) + RESERVE)
        return supervisor.turn_budget

    planned = replan(_still_wrong(outstanding) if outstanding else None)
    _emit_plan(events, PLAN, planned, unanswered=asking.unanswered,
               decided=decided_for_you(part.the_part),
               turn_budget=_size_ceiling(planned.steps))

    rejections = 0
    revisions = _Revisions()
    stuck = {"applied": None}
    described = {}
    standing = Standing()
    looks = _what_this_build_has(tools)
    last_pass = LastPass()

    from core import settings
    auto_approve = settings.instance().get_auto_approve_plan()

    def rewrite(cause, refused, feedback, tried, reason=None, step=None):
        """
        Rewrite the Plan for *cause*, or stop because the budget is spent.

        Seven unrelated things replan the same way: spend one unit of the
        ceiling *step* caused if the cause is attributable to one Step, plan
        again from a fresh Snapshot, and say why on the stream — only the
        cause, sentence and what was tried differ. The Plan being rewritten
        goes with the complaint: without it, a revision wrote from the
        complaint alone, which names only what was wrong.
        """
        if revisions.exhausted(budgets, steps, cause, step=step):
            raise _OutOfRevisions(
                revisions.block_reason(budgets, steps, cause, refused, progress.applied,
                                       step=step), tried)
        revisions.spend(cause, step=step)
        revised = replan(feedback, revising=list(steps))
        last_pass.cause = cause
        _emit_plan(events, PLAN_REVISED, revised, reason=reason or feedback,
                   unanswered=asking.unanswered,
                   decided=decided_for_you(part.the_part),
                   revision=revisions.spent_total(),
                   of=(budgets.revisions if step is not None else revisions_ceiling(budgets, steps)),
                   because=revisions.named(cause),
                   turn_budget=_size_ceiling(revised.steps))
        return revised

    while True:
        steps = planned.steps

        if planned.unbuildable:
            planned = rewrite(_Revisions.OP_REFUSED,
                              "A Step named an operation off the closed list.",
                              _op_off_the_list(planned.unbuildable), progress.tried,
                              reason="; ".join(item["why"] for item in planned.unbuildable))
            continue

        unconsumed = _unconsumed(part.the_part, planned.satisfies,
                                 progress.satisfied)
        if unconsumed:
            planned = rewrite(_Revisions.UNCOVERED,
                              "The Plan still leaves part of the description unplanned.",
                              _plan_the_rest(unconsumed), progress.tried,
                              reason="no Step accounted for " + ", ".join(unconsumed))
            continue

        miscounted = [sum_ for step in steps for sum_ in wrong_sums(step)]
        if miscounted:
            planned = rewrite(_Revisions.MISCOUNTED,
                              "The Plan's arithmetic still does not come out.",
                              _do_the_sums(miscounted), progress.tried,
                              reason="; ".join(miscounted))
            continue

        unread = _assumed(steps) - standing.read
        overruled = {"assumed": len(unread)} if standing.approval and unread else {}
        events.emit(GATE, steps=len(steps),
                    asked=(bool(unread) or not standing.approval) and not auto_approve,
                    **overruled)
        if auto_approve:
            approval = Approval(APPROVE, standing=True)
            standing.approval = True
            standing.read |= _assumed(steps)
            events.emit(GATE, decision=APPROVE, standing=True, auto=True, steps=list(steps),
                        **({"assumed": sorted(unread)} if unread else {}))
        elif standing.approval and not unread:
            approval = Approval(APPROVE, standing=True)
            events.emit(GATE, decision=APPROVE, standing=True, steps=list(steps))
        else:
            approval = gate(list(steps)) or Approval(APPROVE)
            standing.approval = bool(getattr(approval, "standing", False)
                                        and approval.decision == APPROVE)
            if approval.decision == APPROVE:
                standing.read |= _assumed(steps)
                if approval.steps:
                    steps = list(approval.steps)
            events.emit(GATE, decision=approval.decision,
                        standing=standing.approval, steps=list(steps))

        if approval.decision == REJECT:
            applied = progress.applied
            said = "You rejected the Plan, so nothing was applied."
            if applied:
                said = ("You rejected the Plan, so nothing more was applied. The Document keeps "
                        "the %d Step%s an earlier Plan already applied."
                        % (applied, "" if applied == 1 else "s"))
            return _block(events, said, progress.tried, applied)

        if approval.decision != APPROVE:
            planned = rewrite(_Revisions.AMENDED,
                              "Your latest amendment was not planned.",
                              approval.feedback, progress.tried,
                              reason="You asked for: " + approval.feedback)
            continue

        if not steps:
            return _block(events, "The Plan came back with no Steps, so there is nothing to "
                                  "apply and nothing to review.", progress.tried,
                          progress.applied)
        carried = _carried_over(last_pass.steps, steps, last_pass.applied,
                                wording_only=last_pass.cause == _Revisions.UNBUILDABLE)
        if carried < last_pass.applied:
            dropped = last_pass.applied - carried
            depth = tools.call("undo_depth")["depth"]
            if depth < dropped:
                return _block(
                    events,
                    "The revision drops %d Step%s already in the Document, but "
                    "only %d can still be undone -- the undo stack does not "
                    "reach back that far, so nothing was undone."
                    % (dropped, "" if dropped == 1 else "s", depth),
                    progress.tried, progress.applied)
            for _ in range(dropped):
                tools.call("undo_step")
        outcome = _work_through(steps, intent, tools, llm, events, budgets, cancelled,
                                progress, render(part.the_part), looks,
                                planned.acceptance, planned.cites, ledger,
                                start=carried, ops=planned.ops, kernel=kernel,
                                exploration=exploration,
                                design=distilled(part.the_part) if part.the_part else None,
                                supervisor=supervisor)
        last_pass.steps, last_pass.applied = steps, outcome["applied_here"]
        applied = progress.applied

        if outcome.get("budget_exhausted"):
            return _budget_exhausted(events, outcome["budget_exhausted"],
                                     outcome["tried"], applied)

        if outcome["handback"] and stuck["applied"] == applied \
                and not revisions.exhausted(budgets, steps):
            return _block(events, _going_nowhere(
                outcome["handback"], _last_kernel_result(outcome["tried"], events)),
                          outcome["tried"], applied)
        if outcome["handback"]:
            stuck["applied"] = applied

        if outcome["obsolete"] or outcome["handback"]:
            if outcome["obsolete"]:
                cause, feedback = _Revisions.OBSOLETE, outcome["obsolete"]
                refused = "Execute has found it out of date again."
            else:
                cause, feedback = _Revisions.UNBUILDABLE, outcome["handback"]
                refused = outcome["handback"]

            planned = rewrite(cause, refused, feedback, outcome["tried"],
                              step=outcome["applied_here"])
            continue

        described = outcome["description"] or described

        views = _review_views(tools, events)
        suspected = review_suspect(intent, views, described, llm, events, attachments,
                                   supervisor=supervisor)
        events.emit(REVIEW_SUSPECTED, suspected=list(suspected))

        checking = _also_check(_also_check(planned.acceptance, outstanding), suspected)
        not_reached = _not_reached(outcome.get("unbuilt") or [], checking)
        acceptance = [entry for entry in checking if entry not in not_reached]
        checked = assertion(acceptance, described, tools, llm, events, budgets,
                            cancelled, after_a_retry=outcome.get("retried"),
                            description=part.the_part, supervisor=supervisor)
        if checked is not None and checked.geometry:
            regressed = _geometry_regressions(geometry_baseline, checked.geometry,
                                              outcome.get("retried_names"))
            if regressed and not checked.failed:
                traceback = _GEOMETRY_REGRESSION % ", ".join(regressed)
                events.emit(ASSERTION, ok=False, code="", confirmed=checked.confirmed,
                            uncovered=checked.uncovered, traceback=traceback,
                            unmet=regressed)
                checked = Checked(failed=True, confirmed=checked.confirmed,
                                  traceback=traceback, uncovered=checked.uncovered,
                                  unmet=list(checked.unmet) + regressed,
                                  geometry=checked.geometry)
            if not checked.failed:
                geometry_baseline.clear()
                geometry_baseline.update(checked.geometry)
        progress.unmet = (list(checked.unmet) if checked is not None else []) \
            + list(not_reached)
        if checked is not None and checked.failed:
            feedback = _why_the_checks_failed(checked.traceback)
            answer = decided(feedback, applied, unmet=checked.unmet)
            if answer == ACCEPT:
                return LoopResult(ok=True, verdict="pass", steps_applied=applied,
                                  unmet=list(progress.unmet),
                                  reason="You took the result as it is, although the "
                                         "acceptance checks did not pass.")
            if answer == START_OVER:
                feedback += _MAY_START_AGAIN

            planned = rewrite(_Revisions.ASSERTED,
                              "The acceptance checks have failed again.", feedback,
                              outcome["tried"] + [{"assertion": checked.traceback}])
            continue

        verdict, reason = review(intent, views, described, llm, events, attachments,
                                 checked=checked, supervisor=supervisor)
        events.emit(REVIEW_VERDICT, verdict=verdict, reason=reason)

        if verdict != "pass":
            feedback = reason
            answer = decided(reason, applied, unmet=list(progress.unmet))
            if answer == ACCEPT:
                return LoopResult(ok=True, verdict="pass", reason=reason,
                                  steps_applied=applied, unmet=list(progress.unmet))
            if answer == START_OVER:
                feedback += _MAY_START_AGAIN

            rejections += 1
            if rejections > budgets.review_cycles:
                return _block(
                    events,
                    "Review rejected the result %d times; the budget of %d Review cycles per Plan "
                    "is spent." % (rejections, budgets.review_cycles),
                    outcome["tried"] + [{"review": reason}], applied, verdict="fail",
                )

            planned = rewrite(_Revisions.REJECTED, "Review has rejected the result again.",
                              feedback, outcome["tried"] + [{"review": reason}])
            continue

        if checked is not None and checked.uncovered:
            progress.unmet = list(progress.unmet) + list(checked.uncovered)

        return LoopResult(ok=True, verdict=verdict, reason=reason,
                          steps_applied=applied)


def _work_through(steps, intent, tools, llm, events, budgets, cancelled, progress,
                  as_described="", looks=None, entries=(), cites=(), ledger=None,
                  start=0, ops=(), kernel=None, exploration=None, design=None,
                  supervisor=None):
    """
    One pass over the Plan: written by one Execute phase, applied one Step at a
    time. `budgets.attempts_per_step` bounds one Step; a spent Step hands back
    to Plan with its one `Ledger` entry written here, and `ops[i]` rides onto
    `apply_step` as `op` so `core/lint.py` checks the TypeId. `exploration`
    carries `agent.py`'s undeclared-turn count across a same-Step regeneration
    and, threaded in by `_run` across its own replan loop, across a Plan
    revision too, `outcome.apply_failed` here is exhausted from retrying in
    place, and `design`, distilled of its evidence, is carried read-only into
    every Step's own namespace.
    """
    if cancelled():
        raise Stopped()
    described = {}
    attempts = []
    charged = 0
    free_trailing_gaps = 0
    index = 0
    retried = set()
    retried_names = set()
    unbuilt = []
    exploration = {} if exploration is None else exploration
    looks = looks if looks is not None else Looks(cache_path=TYPE_CACHE_PATH, freecad_version=_freecad_version())
    probe, probed_step = _step_exploration(exploration, steps, start)
    written = execute(steps, tools, llm, events, start, budgets=budgets,
                      as_described=as_described, looks=looks, entries=entries,
                      ledger=ledger, kernel=kernel, ops=ops, exploration=probe,
                      design=design, supervisor=supervisor)
    if probed_step is not None:
        exploration[probed_step] = probe["turns"]

    while index < start:
        progress.satisfied.extend(cites[index] if index < len(cites) else ())
        events.emit(APPLIED, step=index + 1, label="", created=[])
        index += 1

    while index < len(steps):
        if cancelled():
            raise Stopped()
        step = steps[index]

        if written.obsolete:
            return {"obsolete": written.obsolete, "handback": None,
                    "description": described, "applied_here": index,
                    "tried": [_tried(step, a) for a in attempts]}

        if not attempts:
            events.emit(STEP_STARTED, index=index + 1, total=len(steps), description=step)

        outcome = written.for_step(index + 1)
        if not outcome.code and written.unbuildable:
            if not _changes_the_shape(step, op=ops[index] if index < len(ops) else None):
                cited = list(cites[index]) if index < len(cites) else []
                unbuilt.append({"step": step, "why": written.unbuildable,
                                "entries": cited})
                events.emit(UNBUILT, step=index + 1, total=len(steps), description=step,
                            why=written.unbuildable, entries=cited)
                index += 1
                attempts = []
                charged = 0
                free_trailing_gaps = 0
                continue
            if written.forced:
                rehearsals = written.rehearsals.get(index + 1, 0)
                spent = (_TURN_BUDGET_SPENT_REHEARSED % (index + 1, len(steps), rehearsals)
                        if rehearsals else _TURN_BUDGET_SPENT % (index + 1, len(steps)))
                return {
                    "handback": None, "obsolete": None, "description": described,
                    "applied_here": index, "tried": [],
                    "budget_exhausted": spent,
                }
            return {
                "handback": "Step %d of %d could not be built as written: %s\n%s"
                            % (index + 1, len(steps), step, written.unbuildable),
                "tried": [], "obsolete": None, "description": described,
                "applied_here": index,
            }

        if outcome.code and not outcome.apply_failed:
            progress.applied += 1
            progress.satisfied.extend(cites[index] if index < len(cites) else ())
            if attempts or outcome.retried:
                retried.add(index)
                retried_names.update(outcome.created)
            described = outcome.description or described
            looks.a_step_landed()
            index += 1
            attempts = []
            charged = 0
            free_trailing_gaps = 0
            continue

        if outcome.apply_failed:
            declared_attempts = [Attempt(code=outcome.code or "", traceback=t)
                                 for t in outcome.retries] or \
                [Attempt(code=outcome.code or "", traceback=outcome.apply_failed)]
            progress.tried = [_tried(step, a) for a in declared_attempts]
            if ledger is not None:
                ledger.record(step, outcome.apply_failed)
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(declared_attempts), step,
                               outcome.apply_failed, _always_the_same_way(declared_attempts)),
                "tried": [_tried(step, a) for a in declared_attempts],
                "obsolete": None, "description": described,
                "applied_here": index,
            }

        ran_dry = _trailing_gap(written, index + 1, len(steps))
        if ran_dry:
            no_code = ("You replied with no code for Step %d, and nothing after it "
                      "either — you %s. Write Step %d and every Step after it, "
                      "shorter this time: no restated Step prose, no comments, "
                      "straight to the calls." % (index + 1, _RAN_OUT_OF_ROOM, index + 1))
        else:
            no_code = ("You replied with no code for Step %d, but wrote code for a "
                      "Step after it — you %s. Write Step %d and every Step after it."
                      % (index + 1, _SKIPPED_A_STEP, index + 1))
        if written.said:
            no_code = "%s\n%s" % (no_code, written.said)
        traceback = no_code
        attempts.append(Attempt(code=outcome.code or "", traceback=traceback))
        progress.tried = [_tried(step, a) for a in attempts]
        events.emit(PREFLIGHT_FAILED, step=index + 1, attempt=len(attempts),
                    traceback=traceback, gate="no_code")
        if ledger is not None:
            ledger.record(step, traceback)

        if ran_dry and free_trailing_gaps < budgets.trailing_gap_retries:
            free_trailing_gaps += 1
        else:
            charged += 1

        if charged >= budgets.attempts_per_step:
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(attempts), step,
                               attempts[-1].traceback, _always_the_same_way(attempts)),
                "tried": [_tried(step, a) for a in attempts],
                "obsolete": None, "description": described,
                "applied_here": index,
            }

        if cancelled():
            raise Stopped()
        probe, probed_step = _step_exploration(exploration, steps, index)
        written = execute(steps, tools, llm, events, index, attempts, budgets, as_described,
                          looks, entries, ledger, kernel=kernel, ops=ops, exploration=probe,
                          supervisor=supervisor)
        if probed_step is not None:
            exploration[probed_step] = probe["turns"]

    return {"obsolete": None, "handback": None, "description": described,
            "applied_here": index,
            "tried": [], "unbuilt": unbuilt, "retried": sorted(
                {cited for at in retried for cited in (cites[at] if at < len(cites)
                                                       else ())}),
            "retried_names": sorted(retried_names)}


def _changes_the_shape(step, op=None):
    """
    Whether the part depends on *step*, preferring the closed nomenclature.

    *op* is `ops[index]`, one of `OP_VERBS` or `None`; a verb settles the
    question outright, and `None` falls back to the same conservative word
    reading of the Step's prose as before.
    """
    if op is not None:
        return op in OP_VERBS
    said = str(step).lower()
    return not (any(word in said for word in _A_PROPERTY_OF_THE_DOCUMENT)
                and not any(word in said for word in _SHAPES_THE_PART))


def _not_reached(unbuilt, acceptance):
    """
    The acceptance entries the Steps that were never built were to satisfy.
    Matched by address, not whole line — a Step cites the address, and the acceptance line carries it with its own value, the allowance `_unconsumed` makes for a Plan copying its line back.

    Taken off what the checks measure, since a check for an unbuilt value would fail and send the run back for a Step it knows cannot be written.
    """
    cited = {_as_written(entry).partition(": ")[0]
             for step in unbuilt for entry in step["entries"]}
    return [entry for entry in acceptance
            if str(entry).partition(": ")[0] in cited]


def _step_exploration(exploration, steps, position):
    """
    `agent.py`'s undeclared-turn count for the Step at *position*, keyed by
    its own prose so a Plan revision that leaves the Step unchanged still
    means the same key, and one that rewrites it starts the count over.
    `position` may run off the end of *steps* -- nothing left to build, a
    pass over a Plan already applied in full -- and reads as a fresh count.
    """
    step = steps[position] if position < len(steps) else None
    return {"turns": exploration.get(step, 0)}, step


def _always_the_same_way(attempts):
    """
    What Plan is told when every attempt at a Step failed for the same reason.

    A recorded run did three identical failures, a replan, and then the same
    three again. Where the failure never varied, the Step itself is what has
    to change.
    """
    if len(attempts) < 2:
        return ""
    for phrase, _directive, _label in _FAILED_THIS_WAY:
        if all(phrase in (attempt.traceback or "") for attempt in attempts):
            return (
                "\n\nEvery one of those %d attempts failed the same way, so this is not a "
                "Step that was written down badly and coded worse — as written, it asks for "
                "something this approach cannot do. Rewording it will send Execute back at "
                "the same wall. Change what the Step asks for: a different operation, a "
                "different order, or the Step split so that the part of it that cannot be "
                "built stands alone." % len(attempts)
            )
    return ""

