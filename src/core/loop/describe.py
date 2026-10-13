"""
Describe the part by reading the drawings multiple times, split into
gather-then-distil. `DESIGN` is emitted from a `finally` around the
reconcile so a raise mid-phase does not cost the log the part it already
understood.
"""
import re
import time
from dataclasses import dataclass, field

from core.agent.phase_contract import BASE_FACES
from core.design import part_file
from core.prompts import read_prompt
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
from core.design.citation_checks import fabricated_citation
from core.design.presence_checks import nobody_carries_the_outside, nothing_changed, unplaced_changes
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
    render,
)
from core.events import ANSWER, DESIGN, PART, QUESTION
from core.sheets import sheet_reader

from core.loop.design import _first_pass, _reconciled, design
from core.loop.gate import ACCEPT, DECIDE_FOR_ME, REVISE, START_OVER
from core.loop.prompt import _pictures, _sheets
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
    noun (a label, a part name). Counting rather than detecting one Cyrillic
    character tells the two apart: refusing the second would cost a Plan
    that was fine over a name it did not invent.
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



def _answered(question, ask, events):
    """Put a question to the person and say on the stream what came back."""
    answer = str(ask(question) or DECIDE_FOR_ME).strip()
    events.emit(ANSWER, text=answer, by="user" if answer else "nobody")
    return answer


def _written_in(the_part, intent, question, answer, llm, events, attachments,
                record_conflicts=True):
    """
    A person's answer, back through Design rather than on to Plan. The
    drawing answering writes `read` and the person answering writes
    `assumed`.
    """
    body = {"intent": intent, "the_part_as_you_described_it": the_part,
            "the_question": question if answer else _settle_it(question)}
    if answer:
        body["the_answer_you_were_given"] = answer
    grew, asks = design(body, llm, events, attachments)
    return merge(the_part, grew, record_conflicts), asks


def _settle_it(question):
    return (question + " Nobody answered it, so settle it yourself: give every value you "
            "choose the flag `assumed` and a `because` saying what you settled it off. It "
            "is shown to the user above the Steps, and a choice they can see is a choice "
            "they can correct.")


def _describe_the_part(intent, llm, events, budgets, ask, cancelled, attachments, base=None):
    """
    The part as Design has it, before anything is planned. A first-pass
    question goes back to the sheet once before it goes to a person, and
    only what the sheet cannot settle reaches the Gate unanswered.
    """
    the_part, question, grouped = _first_pass(intent, llm, events, attachments, cancelled,
                                               budgets.readings, budgets.record_conflicts, base)
    the_part = merge({}, the_part, budgets.record_conflicts)

    if question:
        if cancelled():
            raise Stopped()
        events.emit(QUESTION, text=question)
        grew = {}
        if budgets.rereads and _pictures(attachments):
            grew, _ = design({"intent": intent, "the_part_as_you_described_it": the_part,
                              "the_question": question}, llm, events, attachments)
        if grew:
            the_part = merge(the_part, grew, budgets.record_conflicts)
            events.emit(ANSWER, text=render(grew), by="drawing")
        else:
            the_part, _ = _written_in(the_part, intent, question,
                                      _answered(question, ask, events), llm, events,
                                      attachments, budgets.record_conflicts)

    try:
        if grouped:
            if cancelled():
                raise Stopped()
            grown, _ = _reconciled(the_part, intent, llm, events, attachments)
            the_part = merge(the_part, grown, budgets.record_conflicts)

        the_part = _every_callout_accounted_for(the_part, intent, attachments, llm, events,
                                                budgets, cancelled)
        the_part = _the_outside_is_carried(the_part, intent, attachments, llm, events,
                                           budgets, cancelled)
        the_part = _the_change_is_marked(the_part, intent, attachments, llm, events, budgets,
                                         cancelled, base)
    finally:
        if the_part:
            events.emit(DESIGN, model=the_part, the_base_part=base)
    the_part = _the_sweep_is_the_sheets(the_part, attachments)
    the_part = _the_directions_are_the_drawings(the_part, intent, attachments, llm,
                                                events, budgets, cancelled)
    the_part = _every_number_is_the_sheets(the_part, intent, attachments, llm, events,
                                           budgets, cancelled)
    the_part = _the_citations_are_the_sheets(the_part, attachments, budgets)
    disagreements = _the_geometry_disagrees(attachments, events)
    the_part = merge(the_part, dict(the_part, conflicts=disagreements)
                     if disagreements else the_part, budgets.record_conflicts)
    the_part = _every_direction_settled(the_part, _round_evidence(attachments))
    the_part = _the_tracing_governs(the_part)
    return _written_down(distilled(the_part), each_disagreement_once(the_part),
                         intent, attachments, events)


def _every_number_is_the_sheets(the_part, intent, attachments, llm, events, budgets,
                                cancelled):
    """
    A number on the description that is on no sheet, put back to the
    drawing. Measured over 32 descriptions: 4 to 8 numbers fire per
    sheet; held against the other three drawings, they invent 150 to
    180 in total.
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
            the_part = merge(the_part, grew, budgets.record_conflicts)
        found = _off_the_sheet(attachments, the_part)

    return merge(the_part, {"conflicts": [
        {"about": "the value %g on this part" % number,
         "stands": "no callout prints it, no dimension line measures it and "
                   "nothing traced is that size",
         "was_also_said": "the drawing",
         "kind": A_CONFLICT,
         "machine_written": True}
        for number in found]}, budgets.record_conflicts) if found else the_part


def _off_the_sheet(attachments, the_part):
    """
    The numbers every sheet of this run says are on no sheet.

    `invented` is asked per sheet; answers intersect across attachments — a
    number on the second sheet is on a sheet, even if the first sheet lacks
    it. `_uncited` unions: every callout on every sheet must be accounted for.
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


def _the_citations_are_the_sheets(the_part, attachments, budgets):
    """
    A `from` field whose quoted numerals and marks are on no sheet at
    all, refused into `conflicts` -- the reading itself stays on the
    model, the same way `_every_number_is_the_sheets` above leaves an
    invented number where it was and only records the disagreement.
    Deterministic, so unlike that check it never asks the drawing again.
    No sheet to check against is not evidence either way, the same
    abstention `_off_the_sheet` already makes.
    """
    sheets = _sheets(attachments)
    if not sheets:
        return the_part
    words = {entry.text for sheet in sheets for entry in sheet.printed}
    found = fabricated_citation(the_part, words)
    return merge(the_part, {"conflicts": found}, budgets.record_conflicts) if found else the_part


A_READING_BUDGET = 60.0


def _the_directions_are_the_drawings(the_part, intent, attachments, llm, events,
                                     budgets, cancelled):
    """
    A direction read off the wrong view put back to the drawing.

    Put back to Design with what the sheet measures — the view each circle
    was traced in and at what width. A re-read may overrule, which nothing
    else may: filled stays filled, so a wrong axis cannot talk itself out.
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
        the_part = merge(the_part, grew, budgets.record_conflicts)
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
    A direction still contradicted after re-read and tracing, kept at the
    value it was read at with `flag: assumed`.
    Changes confidence, not value: a `read` a check caught contradicting
    itself is not a reading any more. The whole entry travels, not just
    `value` — dropping `round_in` blinds `grounded`/`crosswise` to it.
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
    The re-read written only where the findings' directions live, and
    nowhere else — everything else merges normally, filling only what is
    empty. The address is `where_the_direction_is`, not the finding's own:
    at `pointing` a direction lands where nothing asked and merge drops it.
    The overrule is earned by the trace, not the finding's own account.
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
    A direction read off the wrong view, settled off the view the circle
    is actually in. The recorded run `pointing` fired eight times, the
    Gate was approved, and all were built along the ring axis; the screw
    came out parallel to it.
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
    A conflict one of whose readings was traced, written into the
    feature. Written through `corrected`, so the displaced reading stays
    under `was` and the Gate shows both. Value is `assumed` since a
    tracing settled it, not the sheet's dimension; `because` is the
    conflict itself.
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
    The one finding a run may not walk past: the shape of the part is
    nobody's. Unsettled, this once let the Plan pad an invented profile
    and the Gate approve it -- a conflict settled by nobody decided for
    itself. The defect was the Plan choosing invisibly: the only conflict
    no later phase can notice.
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
        the_part = merge(the_part, grew, budgets.record_conflicts)
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
    return merge(the_part, grew, budgets.record_conflicts) if grew else the_part


def _unsettled(the_part, base):
    return nothing_changed(the_part) + unplaced_changes(the_part, BASE_FACES.get())


def _the_change_is_marked(the_part, intent, attachments, llm, events, budgets, cancelled,
                          base=None):
    """
    On an edit, a Design with a `changed` feature, each carrying the base-part addresses it changes in `at`; asked again up to `budgets.rereads` times, then refused as a finding in `conflicts`.

    A run without a base part has nothing to mark and is not asked.
    """
    looked = 0
    while base and _unsettled(the_part, base) and looked < budgets.rereads:
        if cancelled():
            raise Stopped()
        looked += 1
        grew, _ = design({"intent": intent, "the_part_as_you_described_it": the_part,
                          "the_question": _MARK_WHAT_CHANGES}, llm, events, attachments)
        if grew:
            the_part = merge(the_part, grew, budgets.record_conflicts)
    found = _unsettled(the_part, base) if base else []
    return merge(the_part, {"conflicts": found}) if found else the_part


_MARK_WHAT_CHANGES = read_prompt("mark_what_changes.md")


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
    The sweep of the sheet, checked against the sheet instead of against
    itself. Held against the text layer, a missed callout fails naming the
    number, where counting the sweep against its own list never would. Only
    where there is a text layer — a raster attachment invents nothing to
    check — and re-reading stops once it stops finding fewer.
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
        the_part = merge(the_part, grew, budgets.record_conflicts)
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
    The views `missed`'s callouts are printed in, or `None` where that is
    not knowable — unplaceable, or printed on a sheet `_sheets` has not
    reached yet. A miss here costs the part, the very failure
    `_every_callout_accounted_for` exists to catch, so `None` keeps the whole
    sheet in play: a false narrowing hides the view the model needs.
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
