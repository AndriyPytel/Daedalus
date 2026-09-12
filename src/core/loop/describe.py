"""Describe the part by reading the drawings multiple times."""
import re
import time
from dataclasses import dataclass, field

from core.design import part_file
from core.design.core import (
    A_CONFLICT,
    UNDER,
    entry_at,
    said_at,
)
from core.design.merge import (
    corrected,
    merge,
)
from core.design.presence_checks import nobody_carries_the_outside
from core.design.orientation_checks import (
    ROUND_IN,
    read_off_the_wrong_view,
    what_the_trace_says,
    where_the_direction_is,
)
from core.design.settlement_checks import settled_by_the_tracing
from core.design.report import (
    distilled,
    each_disagreement_once,
)
from core.events import ANSWER, DESIGN, PART, QUESTION
from core.sheets import sheet_reader

from core.loop.design import _first_pass, _reconciled, design
from core.loop.gate import ACCEPT, DECIDE_FOR_ME, REVISE, START_OVER
from core.loop.prompt import _sheets
from core.loop.results import Stopped
from core.loop.tracing import (
    A_READING_TIMEOUT,
    _a_freecad_of_our_own,
    _every_outline_traced,
    _round_evidence,
    _the_geometry_disagrees,
    _what_the_solver_said,
)



_CYRILLIC = re.compile(r"[\u0400-\u04FF]")


_LATIN_LETTER = re.compile(r"[A-Za-z]")


def _not_english(prose):
    """
    True where Cyrillic outnumbers Latin in a Step's prose — a whole Step
    written in Ukrainian, not a Latin sentence carrying one Cyrillic proper
    noun a user typed into the Intent (a label, a part name). Counting
    rather than merely detecting one Cyrillic character is what tells
    `\"Pad the profile 6.5 mm, label it \u041f\u0440\u043e\u0444\u0456\u043b\u044c\"`
    apart from a Step actually written in Ukrainian: refusing the first
    would cost a Plan that was fine over a name it did not invent.
    """
    cyrillic = len(_CYRILLIC.findall(prose))
    return cyrillic > 0 and cyrillic > len(_LATIN_LETTER.findall(prose))


_DECIDED = {
    ACCEPT: "You took the result as it is.",
    REVISE: "You asked for it to be revised.",
    START_OVER: "You asked it to start over, which lets it delete what was built.",
}


_RAN_OUT_OF_ROOM = "ran out of room before finishing this reply"


_A_SIZE = re.compile(r"(-?\d+(?:\.\d+)?)\s*mm")


_TRUE_TO = 0.5


def _answered(question, ask, events):
    """Put a question to the person and say on the stream what came back."""
    answer = str(ask(question) or DECIDE_FOR_ME).strip()
    events.emit(ANSWER, text=answer, by="user" if answer else "nobody")
    return answer


def _written_in(the_part, intent, question, answer, llm, events, attachments):
    """
    A person's answer, back through Design rather than on to Plan.

    The drawing answering writes `read` and the person answering writes
    `assumed` (ADR-0022).
    """
    body = {"intent": intent, "the_part_as_you_described_it": the_part,
            "the_question": question if answer else _settle_it(question)}
    if answer:
        body["the_answer_you_were_given"] = answer
    grew, asks = design(body, llm, events, attachments)
    return merge(the_part, grew), asks


def _settle_it(question):
    return (question + " Nobody answered it, so settle it yourself: give every value you "
            "choose the flag `assumed` and a `because` saying what you settled it off. It "
            "is shown to the user above the Steps, and a choice they can see is a choice "
            "they can correct.")


def _describe_the_part(intent, llm, events, budgets, ask, cancelled, attachments):
    """
    The part as Design has it, before anything is planned.

    Design owns the question (ADR-0019), and this is the one that survives
    (ADR-0022): where its own model says a value is not merely empty but
    unknowable from the sheet, it asks, once, before any Step has been drafted.
    The run waits, because it is the only question left and so it may afford
    to. Unanswered it is not fatal — the model settles the value like every
    other, and the choice rides to the Gate.

    Design runs whether or not there is a drawing. With nothing to look at it
    describes the part from the words of the Intent alone and asks for what
    those words do not carry, so a text-only run reaches the Gate with a
    description, an Acceptance list and a reading on disk rather than with
    nothing at all.

    It ends in two steps: gather every piece of evidence there is, then distil
    it (ADR-0024). What leaves the phase is the document — what the part is —
    and the evidence that settled it stays in the log beside the file, with
    what nobody agrees on standing there one entry to an address, so every
    phase after this one is handed the part rather than the reading of it.
    `tests/test_loop.py` measures both against the recorded run of the 2023
    pipe clamp.

    `DESIGN` is emitted from a `finally` around the reconcile and the two
    checks that follow it, not after them: a real description already sits
    in `the_part` once gathering starts, and a raise partway through must not
    cost the log the only record of what the run understood the part to be.
    """
    the_part, question, grouped = _first_pass(intent, llm, events, attachments, cancelled)
    the_part = merge({}, the_part)

    if question:
        if cancelled():
            raise Stopped()
        events.emit(QUESTION, text=question)
        the_part, _ = _written_in(the_part, intent, question,
                                  _answered(question, ask, events), llm, events,
                                  attachments)

    try:
        if grouped:
            if cancelled():
                raise Stopped()
            grown, _ = _reconciled(the_part, intent, llm, events, attachments)
            the_part = merge(the_part, grown)

        the_part = _every_callout_accounted_for(the_part, intent, attachments, llm, events,
                                                budgets, cancelled)
        the_part = _the_outside_is_carried(the_part, intent, attachments, llm, events,
                                           budgets, cancelled)
    finally:
        if the_part:
            events.emit(DESIGN, model=the_part)
    the_part = _the_sweep_is_the_sheets(the_part, attachments)
    the_part = _the_directions_are_the_drawings(the_part, intent, attachments, llm,
                                                events, budgets, cancelled)
    the_part = _every_number_is_the_sheets(the_part, intent, attachments, llm, events,
                                           budgets, cancelled)
    disagreements = _the_geometry_disagrees(attachments, events)
    the_part = merge(the_part, dict(the_part, conflicts=disagreements)
                     if disagreements else the_part)
    the_part = _every_direction_settled(the_part, _round_evidence(attachments))
    the_part = _the_tracing_governs(the_part)
    return _written_down(distilled(the_part), each_disagreement_once(the_part),
                         intent, attachments, events)


def _every_number_is_the_sheets(the_part, intent, attachments, llm, events, budgets,
                                cancelled):
    """
    A number on the description that is on no sheet, put back to the drawing
    (ADR-0019).

    Measured over 32 descriptions: 4 to 8 numbers fire per sheet; held against
    the other three drawings, they invent 150 to 180 in total.
    """
    found = _off_the_sheet(attachments, the_part)
    if not found:
        return the_part

    if budgets.rereads:
        if cancelled():
            raise Stopped()
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _where_is_this_number(found)},
                         llm, events, attachments)
        if grew:
            the_part = merge(the_part, grew)
        found = _off_the_sheet(attachments, the_part)

    return merge(the_part, {"conflicts": [
        {"about": "the value %g on this part" % number,
         "stands": "no callout prints it, no dimension line measures it and "
                   "nothing traced is that size",
         "was_also_said": "the drawing",
         "kind": A_CONFLICT,
         "machine_written": True}
        for number in found]}) if found else the_part


def _off_the_sheet(attachments, the_part):
    """
    The numbers every sheet of this run says are on no sheet.

    `invented` is asked of one sheet, which is the right level for it: a sheet
    either prints a number or does not. Over several attachments the answers
    intersect — a number printed on the second sheet is on a sheet, and the
    first sheet not printing it is what a two-sheet drawing looks like.
    `_uncited` unions instead, because every callout on every sheet has to be
    accounted for by the one description.
    """
    said = [set(sheet_reader.invented(sheet, the_part))
            for sheet in _sheets(attachments)]
    return sorted(set.intersection(*said)) if said else []


def _where_is_this_number(found):
    return (
        "These numbers are on your model and on no sheet: %s. Each one is "
        "either printed somewhere you can point at, or measured between two "
        "faces the drawing dimensions, or worked out from numbers that are — "
        "in which case say which, and mark it `derived`. Where it is none of "
        "those, it was assumed, and an assumption belongs in `not_defined` "
        "with what you looked in. Reply with those fields only."
        % ", ".join("%g" % number for number in found))


A_READING_BUDGET = 60.0


def _the_directions_are_the_drawings(the_part, intent, attachments, llm, events,
                                     budgets, cancelled):
    """
    A direction read off the wrong view put back to the drawing.

    The run was stopped by four findings, and all four were the check's own
    fault: two judged a saw cut and cutout as though round, two held the Ø42
    bore to account for a view whose circle the tracer had in pieces.

    The finding is put back to Design with what the sheet measures — the view
    each circle was traced in and at what width. What the drawing does not
    settle, `_every_direction_settled` settles off the same measurement
    (ADR-0022). A re-read may overrule, which nothing else may do — filled
    stays filled, so a wrong axis cannot be talked out of itself.
    """
    round_here = _round_evidence(attachments)
    found = read_off_the_wrong_view(the_part, round_here)
    looked = 0
    while found and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _look_at_the_view_again(found)},
                         llm, events, attachments)
        if not grew:
            break
        the_part = _corrected_by(the_part, grew, found, round_here)
        the_part = merge(the_part, grew)
        still = read_off_the_wrong_view(the_part, round_here)
        if len(still) >= len(found):
            break
        found = still

    if not looked:
        return the_part
    still = read_off_the_wrong_view(the_part, round_here)
    return _left_as_assumed(the_part, still) if still else the_part


def _left_as_assumed(the_part, found):
    """
    A direction still contradicted after every re-read and every tracing, kept
    at the value it was read at with `flag: assumed`.

    Nothing here corrects a value — reaching this point means nobody had a
    measurement to correct it with, which is exactly what `_the_directions_are_
    the_drawings` could not settle. What changes is the confidence the part
    claims for it: a `read` a check has caught contradicting itself is not a
    reading any more, and carrying it as one hides a guess inside a fact the
    Plan and the Gate would otherwise trust without question. The `because` is
    the conflict itself — the words `stands` already holds — so what shows
    above the Steps is why it is a guess, not only that it is one.

    The whole entry travels, not just its `value`: `round_in` is what
    `grounded` and `crosswise` read a feature's direction by, and a write that
    dropped it would settle this finding by blinding the next check to it,
    which is not the same as settling it.
    """
    for finding in found:
        address = where_the_direction_is(the_part, str(finding.get("about") or ""))
        held = entry_at(the_part, address)
        if not isinstance(held, dict) or "value" not in held or held.get("flag") == "assumed":
            continue
        the_part = corrected(the_part, address, dict(held, flag="assumed"),
                             finding["stands"])
    return the_part


def _corrected_by(the_part, grew, found, round_here):
    """
    The re-read written where the directions the findings are about live, and
    nowhere else.

    Every other field of the fragment goes through the ordinary merge and fills
    only what is empty. These were named by a check that measured the sheet, so
    a different answer at one of them is the correction that was asked for
    rather than a second opinion arriving late.

    The address a finding carries is not always the one the answer belongs at,
    which is `where_the_direction_is`. Written at the position `pointing`
    addresses, a direction lands on a field the prompt never asked about, the
    axis stands, the merge drops the reply for being filled already, and the
    re-read ends having changed nothing at all.

    What earns the overrule is the measurement, not the finding's account of
    the reading it stands against — the trace where there is one, and where
    there is none the finding is all the evidence there is.
    """
    for finding in found:
        address = where_the_direction_is(the_part, str(finding.get("about") or ""))
        offered = said_at(grew, address)
        if offered is None or offered == said_at(the_part, address):
            continue
        the_part = corrected(the_part, address, entry_at(grew, address),
                             what_the_trace_says(the_part, address, round_here)
                             or finding.get("stands"))
    return the_part


def _look_at_the_view_again(found):
    return (
        "These directions cannot be right as they are written:\n%s\nA feature is "
        "round in the view its axis points at and in no other, so where the trace "
        "names the view a circle of that width is actually in, that is the view — "
        "say which way the axis points given that view, and correct `round_in` to "
        "it. Where the finding is that two features disagree, one of the two is "
        "misread: say which, and what it should be. Reply with those fields only."
        % "\n".join("  - %s: %s" % (entry["about"], entry["stands"])
                     for entry in found))


def _every_direction_settled(the_part, round_here):
    """
    A direction read off the wrong view, settled off the view the circle is
    actually in (ADR-0022).

    The recorded run `pointing` fired eight times — the Gate was approved,
    and all were built along the ring axis. The screw came out parallel to it.
    """
    for finding in read_off_the_wrong_view(the_part, round_here):
        view = finding.get("instead")
        if not view:
            continue
        address = str(finding["about"])
        the_part = corrected(the_part, address,
                             {"value": _pointing_at(address, view), "flag": "derived",
                              ROUND_IN: view},
                             finding["stands"])
    return the_part


def _the_tracing_governs(the_part):
    """
    A conflict one of whose readings was traced, written into the feature.

    `settled_by_the_tracing` says which conflicts those are and where they
    point; this is the write, through `corrected`, so the reading it displaces
    stays on the model under `was` and the Plan Gate shows both (ADR-0022). The
    value is `assumed`, because a tracing is what the model settled the
    disagreement on and not what the sheet dimensioned, and its `because` is
    the conflict — which stays where it is, in the words of both, for the user
    to overrule at the Gate.
    """
    for finding in settled_by_the_tracing(the_part):
        the_part = corrected(the_part, finding["about"],
                             {"value": finding["instead"], "flag": "assumed"},
                             finding["stands"])
    return the_part


def _pointing_at(address, view):
    """
    The direction a circle traced in *view* gives whatever is written at
    *address* — a profile lies in that view's plane, an axis stands normal to it.

    Which of the two it is, is the field the finding addressed: `axis` or
    `plane`, the last step of the address `_which_field_is_round` built.
    """
    field = address.rsplit(UNDER, 1)[-1]
    return ("the plane of %s" if field == "plane" else
            "perpendicular to the plane of %s") % view


def _the_sweep_is_the_sheets(the_part, attachments):
    """
    `printed_on_the_sheet` put back to what the sheet prints.

    After the re-read loop, never before.
    """
    swept = [entry for path in attachments
             for entry in sheet_reader.printed_on_the_sheet(sheet_reader.read(path))]
    if not the_part or not swept:
        return the_part
    return dict(the_part, printed_on_the_sheet=swept)


def _written_down(document, gathered, intent, attachments, events):
    """
    The document saved as a file, read back out of it, and the evidence beside it.

    Read back rather than passed on, because a file nothing downstream uses is
    a file nobody keeps true.
    """
    source = next((path for path in attachments if sheet_reader.read(path)),
                  attachments[0] if attachments else "")
    path = part_file.saved(document, source, asked_for=intent,
                           read_from=_how_it_was_read(sheet_reader.read(source)))
    if not path:
        return document
    part_file.logged(gathered, path)
    events.emit(PART, path=path)
    return part_file.read(path)


def _how_it_was_read(sheet):
    """What the sheet was measured with, so the reading carries its own terms."""
    if sheet is None:
        return None
    seen = []
    for crop in sheet.crops:
        name = crop.name.split(", part ")[0]
        if name not in seen:
            seen.append(name)
    return {"views": seen, "notes": list(sheet.notes)}


def _the_outside_is_carried(the_part, intent, attachments, llm, events, budgets,
                            cancelled):
    """
    The one finding a run may not walk past: the shape of the part is nobody's.

    The finding stood in `conflicts` saying "no feature carries the outer
    contour" — the Gate showed eight Steps, the user approved them, and the
    Plan padded a profile it invented. A conflict settled by nobody is a
    conflict that decided for itself.

    The defect was never that a profile was chosen, it was that the Plan
    chose one invisibly (ADR-0022). This is the only conflict that says the
    *outside* is missing, and the only one no later phase can notice.
    """
    why = nobody_carries_the_outside(the_part)
    looked = 0
    while why and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _the_outer_profile(why)},
                         llm, events, attachments)
        if not grew:
            break
        the_part = merge(the_part, grew)
        still = nobody_carries_the_outside(the_part)
        if still == why:
            break
        why = still

    why = nobody_carries_the_outside(the_part)
    if not why:
        return the_part

    grew, _ = design({"intent": intent,
                      "the_part_as_you_described_it": the_part,
                      "the_question": _settle_the_outer_profile(why)},
                     llm, events, attachments)
    return merge(the_part, grew) if grew else the_part


def _settle_the_outer_profile(why):
    return (_the_outer_profile(why) + " The sheet has been asked and did not settle it, "
            "so settle it yourself: give every value you choose the flag `assumed` and a "
            "`because` saying what you read it off — the silhouette, the overall sizes, "
            "the end radii. It is shown to the user above the Steps, and a choice they "
            "can see is a choice they can correct.")


def _the_outer_profile(why):
    return (
        "The description says the outside of this part is not square — %s — and no "
        "feature carries that contour, so what would be built is a prism of the overall "
        "sizes. The outer profile is a feature like any other: give it an `id`, a `kind` "
        "naming the shape in an engineer's words, the plane it lies in, a radius for "
        "every arc and a position for every one of those radius centres against a named "
        "datum. Read it off the view the silhouette is drawn in." % why)


def _every_callout_accounted_for(the_part, intent, attachments, llm, events, budgets,
                                 cancelled):
    """
    The sweep of the sheet, checked against the sheet instead of against itself.

    The description is required to account for every callout printed on the
    drawing — each one becomes a value or is named in `not_defined` with a
    reason — and until now the list it was checked against was the model's own
    list of what it had noticed. That check cannot fail in the one way that
    matters: a callout nobody saw is missing from the sweep and from the part,
    and counting one against the other finds nothing wrong.

    Held against the text layer it does fail, and it fails naming the number.
    What comes back goes through the same merge as everything else, so a value
    already read stays read and this can only fill what was empty.

    Only where there is a text layer. A raster attachment supplies no list, and
    this then does nothing at all rather than inventing something to check.

    The re-reading stops where it stops finding fewer: a look that changed
    nothing will not change anything.
    """
    missed = _uncited(attachments, the_part)
    looked = 0
    while missed and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent,
                          "the_part_as_you_described_it": the_part,
                          "the_question": _look_again_at(missed)},
                         llm, events, attachments, views=_views_of(attachments, missed))
        if not grew:
            break
        the_part = merge(the_part, grew)
        still = _uncited(attachments, the_part)
        if len(still) >= len(missed):
            break
        missed = still
    return the_part


def _uncited(attachments, the_part):
    missed = []
    for sheet in _sheets(attachments):
        missed.extend(sheet_reader.uncited(sheet, the_part))
    return missed


def _views_of(attachments, missed):
    """
    The views `missed`'s callouts are printed in, or `None` where that is not
    knowable — a callout `sheet_reader.dimensions` cannot place on a named
    view, or one this sweep does not find at all, most often because it is
    printed on a second attached sheet the loop over `_sheets` has not reached
    yet when it does.

    A miss here costs the part — the very failure `_every_callout_accounted_for`
    exists to catch — so `None` keeps the whole sheet in play rather than
    narrow to a guess: a false alarm on the narrowing costs one extra view's
    worth of pictures, and a wrong narrowing hides the one the model needs.
    """
    wanted = set(missed)
    views = set()
    for sheet in _sheets(attachments):
        for entry in sheet_reader.dimensions(sheet):
            if entry.text in wanted:
                wanted.discard(entry.text)
                if not entry.seen_in:
                    return None
                views.add(entry.seen_in)
    return None if wanted or not views else views


def _look_again_at(missed):
    return (
        "These callouts are printed on the sheet's own text layer and no value "
        "in your model uses them: %s. Each one is a dimension the draughtsman "
        "put there, so each one belongs somewhere — on a feature, on the "
        "envelope, or in `not_defined` with the views you looked in and the "
        "reason it could not be used. Look at the views again and follow each "
        "leader to the feature its arrowhead touches. Where a number is already "
        "on your model under a different reading, leave it: reply only with "
        "what is missing." % ", ".join(sorted(set(missed))))


_A_DIRECTION_FIELD = (UNDER + "plane", UNDER + "axis")


_INVENTED = "assum"
