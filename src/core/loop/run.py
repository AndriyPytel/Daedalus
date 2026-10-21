"""The main loop runner and coordination logic."""
import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field

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
    DISPUTED,
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
from core.kernel import Kernel
from core.ledger import Ledger
from core.llm import ProviderRefused

from core.agent.phase_contract import the_base_faces_of
from core.loop.assertion import (
    Checked,
    _GEOMETRY_REGRESSION,
    _geometry_regressions,
    _why_the_checks_failed,
    assertion,
)
from core.loop.budgets import Budgets, PhaseError, _Revisions, revisions_ceiling
from core.loop.describe import _DECIDED, _RAN_OUT_OF_ROOM, _describe_the_part, the_change_refused
from core.loop.execute import (
    OP_VERBS,
    Attempt,
    Looks,
    TYPE_CACHE_PATH,
    _A_PROPERTY_OF_THE_DOCUMENT,
    _FAILED_THIS_WAY,
    _MAY_START_AGAIN,
    _ROUNDS,
    _SHAPES_THE_PART,
    _SKIPPED_A_STEP,
    _THICKNESS_FAILED,
    _TURN_BUDGET_SPENT,
    _TURN_BUDGET_SPENT_REHEARSED,
    _WHAT_NOW,
    _emit_plan,
    _freecad_version,
    _kind_of_step,
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
    _bad_rollback,
    _cited_without_a_number,
    _fillet_before_a_cut,
    _no_number_to_build_on,
    _round_after_the_cuts,
    _value_undefined,
    _op_off_the_list,
    _plan_once_it_knows_enough,
    _plan_the_rest,
    _unconsumed,
)
from core.loop.results import LoopResult, Progress, Stopped, _OutOfRevisions
from core.loop.helpers import (
    _assumed, _still_wrong, _rollback_of, _mark_rolled_back, _also_check,
    _trailing_gap, _tried, _block, _stopped, _aborted, _budget_exhausted,
    _going_nowhere, _last_kernel_result, _disputes_of_unmet,
)
from core.agent.reader import DRAWING_VIEWS, views_beside_crops
from core.agent.subagent import RESERVE
from core.loop.document import copied
from core.nobody import nobody_answering, nobody_is_answering
from core.prompts import read_prompt
from core.loop.review import review, review_suspect


class _Asking:
    """What has been asked in this run, and of whom. Run-wide rather than per Plan."""

    def __init__(self):
        self.unanswered = []
        self.looked_up = []
        self.read_again = 0
        self.settled = 0
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
        with nobody_answering() if ask is nobody_to_ask else contextlib.nullcontext(), \
                the_base_faces_of(tools):
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
    Every Step of the active timeline this run has reached so far -- the
    text, and what each cites -- with `applied` how many of them actually
    landed in the Document. A revision is handed the head of `steps` up to
    `applied` as History, read-only, and writes only the continuation.
    """
    steps: object = None
    cites: object = None
    applied: int = 0


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

    kernel = kernel or Kernel()

    base = tools.call("base_inventory")["inventory"]
    if the_part_path and not attachments:
        part = Part(part_file.read(the_part_path))
    else:
        part = Part(_describe_the_part(intent, llm, events, budgets, ask, cancelled,
                                       attachments, base))
        written = events.last(PART)
        progress.the_part_path = written.data["path"] if written else ""
        refused = the_change_refused(part.the_part, base)
        if refused:
            return _block(events, "The description of this edit is refused, so nothing was planned: "
                                  + refused, [], 0)

    asking = _Asking()
    ledger = Ledger(_FAILED_THIS_WAY)
    geometry_baseline = {}
    exploration = {}
    supervisor = SessionTreeSupervisor()

    def replan(feedback=None, revising=(), history=()):
        planned, part.the_part = _plan_once_it_knows_enough(
            intent, tools.call("snapshot"), llm, events, budgets, part.the_part,
            cancelled, asking, tools, feedback, attachments, revising, history, ledger,
            supervisor, kernel=kernel, base=base)
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
    history_log = []
    frozen_values = {}

    from core import settings
    auto_approve = settings.instance().get_auto_approve_plan()

    last_complaint = []
    patched = set()
    numberless = set()
    plan_unmet = []
    rounds_patched = set()
    rollback_patched = [False]
    batched = [False]
    fired = []
    skipped = []

    def rewrite(cause, refused, feedback, tried, reason=None, step=None, patch=False):
        """
        Rewrite the Plan for *cause*, or stop because the budget is spent.

        Seven unrelated things replan the same way: spend one unit of the ceiling
        *step* caused, plan again from a fresh Snapshot with the Plan being rewritten
        beside the complaint, and say why on the stream. A *patch* spends no revision
        and its callers bound it.
        """
        if not patch and revisions.exhausted(budgets, steps, cause, step=step):
            raise _OutOfRevisions(
                revisions.block_reason(budgets, steps, cause, refused, progress.applied,
                                       step=step), tried)
        if not patch:
            revisions.spend(cause, step=step)
        last_complaint[:] = [reason or feedback]
        if progress.applied:
            revised = replan(feedback, history=list(last_pass.steps[:progress.applied]))
        else:
            revised = replan(feedback, revising=list(steps))
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
        rollback = _rollback_of(planned)
        continuation = steps
        to_step, rollback_reason = rollback if rollback else (last_pass.applied, None)

        if planned.unbuildable:
            planned = rewrite(_Revisions.OP_REFUSED,
                              "A Step named an operation off the closed list.",
                              _op_off_the_list(planned.unbuildable), progress.tried,
                              reason="; ".join(item["why"] for item in planned.unbuildable))
            continue

        if planned.bad_rollback:
            free = not rollback_patched[0]
            rollback_patched[0] = True
            planned = rewrite(_Revisions.OP_REFUSED,
                              "The Plan's rollback field could not be used.",
                              _bad_rollback(planned.bad_rollback), progress.tried,
                              reason=planned.bad_rollback, patch=free)
            continue
        rollback_patched[0] = False

        satisfied_so_far = progress.satisfied
        if rollback:
            dropped_cites = {entry for index in range(to_step, last_pass.applied)
                             for entry in (last_pass.cites[index]
                                          if last_pass.cites and index < len(last_pass.cites)
                                          else ())}
            satisfied_so_far = [entry for entry in progress.satisfied
                               if entry not in dropped_cites]
        unconsumed = [entry for entry in
                      _unconsumed(part.the_part, planned.satisfies, satisfied_so_far)
                      if entry not in plan_unmet]
        for entry in unconsumed:
            if entry in patched or nobody_is_answering():
                plan_unmet.append(entry)
        unconsumed = [entry for entry in unconsumed if entry not in plan_unmet]
        progress.unmet = list(dict.fromkeys(list(progress.unmet) + plan_unmet))
        if unconsumed:
            patched.update(unconsumed)
            planned = rewrite(_Revisions.UNCOVERED,
                              "The Plan still leaves part of the description unplanned.",
                              _plan_the_rest(unconsumed, history=bool(last_pass.steps)),
                              progress.tried,
                              reason="no Step accounted for " + ", ".join(unconsumed),
                              patch=True)
            continue

        early = []
        for item in _fillet_before_a_cut(planned.steps, planned.ops):
            unmet_round = "round planned before a cut: " + item["step"]
            if item["step"] in rounds_patched:
                if unmet_round not in plan_unmet:
                    plan_unmet.append(unmet_round)
            elif unmet_round not in plan_unmet:
                early.append(item)
        progress.unmet = list(dict.fromkeys(list(progress.unmet) + plan_unmet))
        if early:
            rounds_patched.update(item["step"] for item in early)
            planned = rewrite(_Revisions.OP_REFUSED,
                              "A fillet, chamfer or draft is planned before a cut with no reason.",
                              _round_after_the_cuts(early, planned.steps), progress.tried,
                              reason="round before a cut: " + "; ".join(item["step"] for item in early),
                              patch=True)
            continue

        no_number = _cited_without_a_number(part.the_part, planned.cites)
        if no_number and nobody_is_answering():
            planned.cites = [[entry for entry in cited
                              if _as_written(entry).partition(": ")[0] not in no_number]
                             for cited in planned.cites]
            planned.satisfies = [entry for entry in planned.satisfies
                                 if _as_written(entry).partition(": ")[0] not in no_number]
            no_number = []
        if no_number:
            again = bool(numberless & set(no_number))
            numberless.update(no_number)
            planned = rewrite(_Revisions.VALUE_UNDEFINED,
                              "A Step cites an address Design left without its number.",
                              _no_number_to_build_on(no_number), progress.tried,
                              reason="no number at " + ", ".join(no_number),
                              patch=not again)
            continue

        value_errors = list(planned.value_errors)
        landed = " ".join(last_pass.steps[:progress.applied]) if last_pass.steps else ""
        for name, value in planned.values.items():
            if (name in frozen_values and frozen_values[name] != value
                    and re.search(r"\b%s\b" % re.escape(name), landed)):
                value_errors.append({"step": name,
                                     "why": "is already frozen at %s for an earlier Step; "
                                            "give this one a new name" % frozen_values[name]})
        if value_errors:
            planned = rewrite(_Revisions.VALUE_UNDEFINED,
                              "The Plan named a value its kernel does not have.",
                              _value_undefined(value_errors), progress.tried,
                              reason="; ".join("%s %s" % (item["step"], item["why"])
                                              for item in value_errors))
            continue
        frozen_values.update(planned.values)

        gate_context = {"history": list(history_log)} if history_log else {}
        if rollback:
            gate_context["rollback"] = {"to_step": to_step, "reason": rollback_reason}

        unread = _assumed(continuation) - standing.read
        overruled = {"assumed": len(unread)} if standing.approval and unread else {}
        events.emit(GATE, steps=len(continuation),
                    asked=(bool(unread) or not standing.approval) and not auto_approve,
                    **overruled, **gate_context)
        if auto_approve:
            approval = Approval(APPROVE, standing=True)
            standing.approval = True
            standing.read |= _assumed(continuation)
            events.emit(GATE, decision=APPROVE, standing=True, auto=True,
                        steps=list(continuation),
                        **({"assumed": sorted(unread)} if unread else {}), **gate_context)
        elif standing.approval and not unread:
            approval = Approval(APPROVE, standing=True)
            events.emit(GATE, decision=APPROVE, standing=True, steps=list(continuation),
                        **gate_context)
        else:
            approval = gate(list(continuation), history=list(history_log),
                            rollback=(dict(gate_context["rollback"], upto=last_pass.applied)
                                      if rollback else None)) or Approval(APPROVE)
            standing.approval = bool(getattr(approval, "standing", False)
                                        and approval.decision == APPROVE)
            if approval.decision == APPROVE:
                standing.read |= _assumed(continuation)
                if approval.steps:
                    continuation = list(approval.steps)
            events.emit(GATE, decision=approval.decision,
                        standing=standing.approval, steps=list(continuation), **gate_context)

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

        revising_built_part = bool(progress.applied and last_complaint)
        disputed = _disputes_of_unmet(planned.disputed, progress.unmet) if revising_built_part else []
        if disputed:
            events.emit(DISPUTED, entries=[item["entry"] for item in disputed],
                        measured=[item["measured"] for item in disputed],
                        because=[item["because"] for item in disputed])
        undisputed = [entry for entry in dict.fromkeys(progress.unmet)
                      if entry not in {item["entry"] for item in disputed}]
        if not steps and not rollback and revising_built_part:
            if disputed and not undisputed:
                return LoopResult(ok=True, steps_applied=progress.applied,
                                  unmet=list(progress.unmet), disputed=disputed)
            return _block(events, "The revision added no Step to the %d already built, so "
                                  "what it was asked to fix still stands: %s%s"
                          % (progress.applied, last_complaint[0],
                             ("\nDisputed: %s\nNeither fixed nor disputed: %s"
                              % ("; ".join(item["entry"] for item in disputed),
                                 "; ".join(undisputed))) if disputed else ""),
                          progress.tried, progress.applied)
        if not steps and not rollback:
            return _block(events, "The Plan came back with no Steps, so there is nothing to "
                                  "apply and nothing to review.", progress.tried,
                          progress.applied)
        if rollback:
            dropped = last_pass.applied - to_step
            depth = tools.call("undo_depth")["depth"]
            if depth < dropped:
                return _block(
                    events,
                    "The revision rolls back %d Step%s to Step %d, but only %d can "
                    "still be undone -- the undo stack does not reach back that far, "
                    "so nothing was undone."
                    % (dropped, "" if dropped == 1 else "s", to_step, depth),
                    progress.tried, progress.applied)
            for _ in range(dropped):
                tools.call("undo_step")
            _mark_rolled_back(history_log, to_step, last_pass.applied, rollback_reason)
            progress.satisfied = satisfied_so_far
        carried = to_step
        full_steps = (list(last_pass.steps[:carried]) if last_pass.steps else []) + continuation
        full_cites = (list(last_pass.cites[:carried]) if last_pass.cites else []) + list(planned.cites)
        full_ops = [None] * carried + list(planned.ops)
        full_changes = [[]] * carried + list(planned.changes)
        outcome = _work_through(full_steps, intent, tools, llm, events, budgets, cancelled,
                                progress, render(part.the_part), looks,
                                planned.acceptance, full_cites, ledger,
                                start=carried, ops=full_ops, kernel=kernel,
                                exploration=exploration,
                                design=distilled(part.the_part) if part.the_part else None,
                                supervisor=supervisor, plan_values=dict(frozen_values),
                                base=base, changes=full_changes, settled=batched[0],
                                attachments=attachments, compare=fired)
        order = list(range(carried)) + outcome["landed"]
        order += [at for at in range(len(full_steps)) if at not in order]
        full_steps = [full_steps[at] for at in order]
        full_cites = [full_cites[at] for at in order]
        full_ops = [full_ops[at] for at in order]
        full_changes = [full_changes[at] for at in order]
        batched[0] = batched[0] or bool(outcome.get("deferred"))
        for offset in range(outcome["applied_here"] - carried):
            history_log.append({"step": carried + offset + 1, "text": full_steps[carried + offset],
                               "rolled_back": None})
        last_pass.steps, last_pass.cites, last_pass.applied = \
            full_steps, full_cites, outcome["applied_here"]
        applied = progress.applied
        for item in outcome.get("unbuilt") or ():
            progress.satisfied.extend(item["entries"])
        skipped.extend(outcome.get("unbuilt") or ())

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
                if _THICKNESS_FAILED in feedback:
                    alternative = next(d for p, d, _ in _FAILED_THIS_WAY if p == _THICKNESS_FAILED)
                    feedback = "%s\n\nAlternative: %s" % (feedback, alternative)
                refused = outcome["handback"]

            planned = rewrite(cause, refused, feedback, outcome["tried"],
                              step=outcome["applied_here"])
            continue

        if nobody_is_answering() and not applied:
            return _block(events, "No Step could be built: %s" % "; ".join(
                "%s (%s)" % (item["step"], item["why"]) for item in skipped),
                          outcome["tried"], applied)

        described = outcome["description"] or described

        if nobody_is_answering():
            unbuilt = skipped
            progress.unmet = list(dict.fromkeys(
                _not_reached(unbuilt, planned.acceptance) + plan_unmet))
            kept = _the_last_valid_shape(tools, events, applied)
            return LoopResult(ok=True, steps_applied=kept, unmet=list(progress.unmet),
                              reason="; ".join(["Step not built: %s (%s)" % (item["step"], item["why"])
                                                for item in unbuilt]
                                               + (["final shape invalid, saved Step %d" % kept]
                                                  if kept < applied else [])))

        views = _review_views(tools, events)
        suspected = review_suspect(intent, views, described, llm, events, attachments,
                                   supervisor=supervisor)
        events.emit(REVIEW_SUSPECTED, suspected=list(suspected))

        checking = _also_check(_also_check(planned.acceptance, outstanding), suspected)
        not_reached = _not_reached(outcome.get("unbuilt") or [], checking)
        acceptance = [entry for entry in checking if entry not in not_reached]
        checked = assertion(acceptance, described, tools, llm, events, budgets,
                            cancelled, after_a_retry=outcome.get("retried"),
                            description=part.the_part, supervisor=supervisor, base=base,
                            not_checked=planned.left_out)
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
            + list(not_reached) + plan_unmet
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
                                 checked=checked, supervisor=supervisor, base=base)
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
                  supervisor=None, plan_values=None, base=None, changes=(), settled=False,
                  attachments=(), compare=None):
    """
    One pass over the Plan: written by one Execute phase, applied one Step at a
    time. `budgets.attempts_per_step` bounds one Step; a spent Step hands back
    to Plan with its one `Ledger` entry written here, and `ops[i]` rides onto
    `apply_step` as `op` so `core/lint.py` checks the TypeId; `exploration`
    carries `agent.py`'s undeclared-turn count across a same-Step regeneration
    and across a Plan revision, `design` and `plan_values` ride read-only into
    every Step's namespace. Where nobody answers, a failed round is left out at
    once and a failed material Step waits for one batched handback unless that is *settled*.
    """
    if cancelled():
        raise Stopped()
    described = {}
    deferred = []
    landed = []
    runs_of = {}
    attempts = []
    charged = 0
    free_trailing_gaps = 0
    index = 0
    retried = set()
    retried_names = set()
    unbuilt = []
    exploration = {} if exploration is None else exploration
    looks = looks if looks is not None else Looks(cache_path=TYPE_CACHE_PATH, freecad_version=_freecad_version())

    def written_from(position):
        probe, probed_step = _step_exploration(exploration, steps, position)
        written = execute(steps, tools, llm, events, position, budgets=budgets,
                          as_described=as_described, looks=looks, entries=entries,
                          ledger=ledger, kernel=kernel, ops=ops, exploration=probe,
                          design=design, supervisor=supervisor, plan_values=plan_values,
                          base=base, changes=changes, runs_of=runs_of)
        if probed_step is not None:
            exploration[probed_step] = probe["turns"]
        return written

    def comparison_follows():
        """Where nobody answers, once per run: Execute is shown the part beside the drawing and writes what it lacks as further Steps, which `steps` takes on."""
        nonlocal written, index, attempts, charged, free_trailing_gaps
        if compare is None or compare or not nobody_is_answering() or deferred or cancelled() \
                or not progress.applied:
            return False
        compare.append(True)
        count = len(steps)
        with tempfile.TemporaryDirectory(prefix="fcc-compare-") as renders, copied(tools) as document:
            shown = views_beside_crops(kernel, None, [{"view": view} for view in DRAWING_VIEWS],
                                       renders, document=document, crops_only=False)
        if supervisor is not None:
            supervisor.turn_budget += RESERVE
        probe, _ = _step_exploration(exploration, steps, count)
        later = execute(steps + [_COMPARE], tools, llm, events, count, budgets=budgets,
                        as_described=as_described, looks=looks, entries=entries, ledger=ledger,
                        kernel=kernel, ops=ops, exploration=probe, design=design,
                        supervisor=supervisor, plan_values=plan_values, base=base,
                        changes=changes, attachments=attachments, beside=shown, runs_of=runs_of)
        added = sorted(number for number in later.steps if number > count)
        for number in added:
            steps.append("Further Step from comparing with the drawing: %s" % (
                later.steps[number].label or "build what the part lacks"))
            cites.append([])
            ops.append(None)
            changes.append([])
        if added:
            written = later
            attempts, charged, free_trailing_gaps = [], 0, 0
        return bool(added)

    written = written_from(start)

    def leave_out(why):
        """The Step at `index` is passed over, and Execute is asked for the rest from the next one."""
        nonlocal index, attempts, charged, free_trailing_gaps, written
        cited = list(cites[index]) if index < len(cites) else []
        op = ops[index] if index < len(ops) else None
        if not settled and _kind_of_step(steps[index], op) not in _ROUNDS \
                and _changes_the_shape(steps[index], op=op):
            deferred.append({"index": index, "why": why})
        else:
            unbuilt.append({"step": steps[index], "why": why, "entries": cited})
            events.emit(UNBUILT, step=index + 1, total=len(steps), description=steps[index],
                        why=why, entries=cited)
        index += 1
        attempts, charged, free_trailing_gaps = [], 0, 0
        if index < len(steps) and not any(written.for_step(number).code
                                          for number in range(index + 1, len(steps) + 1)):
            written = written_from(index)

    while index < start:
        progress.satisfied.extend(cites[index] if index < len(cites) else ())
        events.emit(APPLIED, step=index + 1, label="", created=[])
        index += 1

    while index < len(steps) or comparison_follows():
        if cancelled():
            raise Stopped()
        step = steps[index]

        if written.obsolete and deferred and nobody_is_answering():
            return {"obsolete": None, "description": described, "tried": [], "unbuilt": unbuilt,
                    "applied_here": start + len(landed), "landed": list(landed), "deferred": True,
                    "handback": _could_not_be_built(steps, deferred, written.obsolete)}

        if written.obsolete:
            return {"obsolete": written.obsolete, "handback": None,
                    "description": described, "applied_here": start + len(landed), "landed": list(landed),
                    "tried": [_tried(step, a) for a in attempts]}

        if not attempts:
            events.emit(STEP_STARTED, index=index + 1, total=len(steps), description=step)

        outcome = written.for_step(index + 1)
        if not outcome.code and str(index + 1) in written.left_out and nobody_is_answering():
            leave_out(written.left_out[str(index + 1)])
            continue
        if not outcome.code and nobody_is_answering() and any(
                written.for_step(number).code for number in range(index + 2, len(steps) + 1)):
            leave_out(written.unbuildable or "Execute went past it")
            continue
        if not outcome.code and written.unbuildable:
            if nobody_is_answering() and not written.forced:
                leave_out(written.unbuildable)
                continue
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
                    "applied_here": start + len(landed), "landed": list(landed), "tried": [],
                    "budget_exhausted": spent,
                }
            return {
                "handback": "Step %d of %d could not be built as written: %s\n%s"
                            % (index + 1, len(steps), step, written.unbuildable),
                "tried": [], "obsolete": None, "description": described,
                "applied_here": start + len(landed), "landed": list(landed),
            }

        if outcome.code and not outcome.apply_failed:
            progress.applied += 1
            landed.append(index)
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
            if nobody_is_answering():
                leave_out(outcome.apply_failed)
                continue
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(declared_attempts), step,
                               outcome.apply_failed, _always_the_same_way(declared_attempts)),
                "tried": [_tried(step, a) for a in declared_attempts],
                "obsolete": None, "description": described,
                "applied_here": start + len(landed), "landed": list(landed),
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
            if nobody_is_answering():
                leave_out(attempts[-1].traceback)
                continue
            return {
                "handback": "Step %d of %d could not be built after %d attempts: %s\n"
                            "The last traceback was:\n%s%s"
                            % (index + 1, len(steps), len(attempts), step,
                               attempts[-1].traceback, _always_the_same_way(attempts)),
                "tried": [_tried(step, a) for a in attempts],
                "obsolete": None, "description": described,
                "applied_here": start + len(landed), "landed": list(landed),
            }

        if cancelled():
            raise Stopped()
        probe, probed_step = _step_exploration(exploration, steps, index)
        written = execute(steps, tools, llm, events, index, attempts, budgets, as_described,
                          looks, entries, ledger, kernel=kernel, ops=ops, exploration=probe,
                          supervisor=supervisor, base=base, changes=changes)
        if probed_step is not None:
            exploration[probed_step] = probe["turns"]

    if deferred:
        return {"obsolete": None, "description": described, "tried": [], "unbuilt": unbuilt,
                "applied_here": start + len(landed), "landed": list(landed), "deferred": True,
                "handback": _could_not_be_built(steps, deferred)}
    return {"obsolete": None, "handback": None, "description": described,
            "applied_here": start + len(landed), "landed": list(landed),
            "tried": [], "unbuilt": unbuilt, "retried": sorted(
                {cited for at in retried for cited in (cites[at] if at < len(cites)
                                                       else ())}),
            "retried_names": sorted(retried_names)}


def _the_last_valid_shape(tools, events, applied):
    """
    Undoes Steps from the end until the Document's solid is a valid shape, and returns how many Steps are left.

    The first Step is never undone: a Document with no solid has no STEP to save.
    """
    if "shape_valid" not in tools:
        return applied
    kept = applied
    while kept > 1 and not tools.call("shape_valid")["valid"]:
        tools.call("undo_step")
        kept -= 1
    if kept < applied:
        events.emit(ANSWER, by="nobody", text="final shape invalid, saved Step %d" % kept)
    return kept


_COMPARE = read_prompt("compare_with_the_drawing.md").strip()


def _could_not_be_built(steps, deferred, obsolete=""):
    """What Plan is told about the material Steps Execute could not build in one pass, with its reason for each and, if any, why the rest then looked out of date."""
    return ("These Steps could not be built as written. Rewrite each so it can be built, "
            "using the reason given, and send it back among the Steps still to build; "
            "what is already built is History.\n"
            + "\n".join("- %s\n  Execute: %s" % (steps[item["index"]], item["why"])
                       for item in deferred)
            + ("\nExecute then found the Steps after them out of date: %s" % obsolete
               if obsolete else "")
            + "\nWhere the reason is a number the description does not give, choose that "
              "number, bind it as a value in your kernel and name it in the Step's values, "
              "and keep the Step's extent: a rewritten Step removes or adds the same material "
              "as the one it replaces, never a smaller feature in its place. Where the reason is a "
              "shape -- an outline whose numbers do not say which edge or corner each belongs to "
              "-- compute that outline yourself in your kernel as a closed list of points, "
              "choosing what the numbers leave open, and name it in the Step's values: Execute "
              "draws what you computed and never arranges numbers itself.")


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

