"""FreeCAD geometry tracing and disagreement detection."""
import re
import time

from core import kernel
from core.design.design import A_CHECK_HAZARD
from core.events import ERROR
from core.sheets import sheet_reader, sketch_check


A_READING_TIMEOUT = 60
A_READING_BUDGET = 60.0


def _the_geometry_disagrees(attachments, events, budget=A_READING_BUDGET):
    """
    What the sheet prints against what the sheet draws, as conflicts.

    The reading is a closed walk of lines and arcs in millimetres now
    (FCC-118), the sheet's own distances reach the faces they measure
    (FCC-121), and `sketch_check` builds the one against the other. Nothing
    consumed the verdict: a sheet whose digit was misread by 8 mm arrived at
    the Plan Gate exactly as silent as a sheet that agrees with itself.

    Recorded, never settled, and no ladder. Which of the two is wrong — the
    digit, or a face the tracer took to the inside of its own line — is not
    something a re-read can be asked and not something this can decide, so it
    goes on the part in the words of both and the user settles it at the Gate.
    That is what `conflicts` is, and it is the only phase that has both numbers
    in front of it.

    Measured on `tests/modelmania`: 11 printed distances reach a traced outline
    across the four sheets, and none becomes a disagreement. A run against them
    gains no conflict, which is the right answer rather than a weak check: the
    sheets agree with themselves.

    And it is a floor rather than a net. A dimension reaches the geometry
    through the feet of its own extension lines, so a tracing further out than
    `sketch_check.A_FOOT` has no face for the foot to name and there is nothing
    to compare — this cannot catch a digit misread by 8 mm, and widening the
    band to reach one writes wrong faces instead: measured, a band of a tenth
    of the printed value produces three disagreements of 4.5 to 4.9 mm on
    sheets known to agree with themselves. The misread digit is caught where it
    needs no correspondence at all, by the sheet measuring its own numbers
    (`Printed.measures`) and by `uncited`.

    An outline the solver could not be asked about arrives on the part as a
    check hazard, for the same reason a re-read that found nothing says so: a
    sheet nothing was wrong with and a sheet nobody could check both arrive at
    the Gate with nothing to settle on them, and only one of the two is a
    verdict. It stays on the `ERROR` stream as well, which is where a run is
    read back from and not where a reading is settled. A child that never came
    up, a child that died and could not be replaced, and a budget that ran out
    are all that same sentence: those outlines are unchecked and the run goes
    on. Nothing here may end a run — that is what makes it a reading check and
    not a Step.

    `budget` is the phase's, not the outline's. A timeout each is no bound at
    all: a sweep can spend one timeout for every outline without returning a
    verdict. The four sheets contain 142 outlines, so the phase's minute is a
    child that has stopped answering rather than a normal reading time.
    """
    traced = _every_outline_traced(attachments)
    if not traced:
        return []

    worker = _a_freecad_of_our_own(events)
    found = []
    if worker is None:
        unchecked = [view for outline, view, _ in traced if outline.entities]
    else:
        unchecked = []
        spent_by = time.monotonic() + budget
        try:
            for outline, view, between in traced:
                left = spent_by - time.monotonic()
                if left <= 0:
                    if outline.entities:
                        unchecked.append(view)
                    continue
                found.extend(_what_the_solver_said(
                    worker, outline, view, between, unchecked,
                    min(left, kernel.DEFAULT_TIMEOUT)))
        finally:
            worker.kill()
    if unchecked:
        events.emit(ERROR, message="The solver could not be asked about %d of the "
                                   "outlines read off these sheets: %s. The run goes "
                                   "on; those outlines are unchecked."
                                   % (len(unchecked), ", ".join(sorted(set(unchecked)))))
        found.extend({"about": "the line work traced in %s" % view,
                      "stands": "the solver could not be asked about it, so nothing "
                                "here says whether the view draws what the sheet "
                                "prints",
                      "kind": A_CHECK_HAZARD,
                      "machine_written": True}
                     for view in sorted(set(unchecked)))
    return found


def _every_outline_traced(attachments):
    """
    Every outline read off every sheet, with the view and the distances it
    belongs to, before any child is started.

    Read first so that a child which never comes up still knows what it was
    going to be asked about: "nothing was checked" is only sayable by something
    holding the list of what there was.
    """
    traced = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            continue
        for drawn in sheet.drawn:
            traced.extend((outline, drawn.view, sheet.between)
                          for outline in drawn.outlines)
    return traced


def _what_the_solver_said(worker, outline, view, between, unchecked, timeout=None):
    """
    One outline's verdict, as the conflicts it puts on the description.

    A constraint the solver calls redundant reads the same way here as one it
    calls conflicting: both are a reading nobody in this phase can settle —
    which of the two printed numbers to drop is not something a re-read can be
    asked — so both go to the Gate, in the same words.
    """
    verdict = sketch_check.checked_by(worker, outline, view, between, timeout)
    if verdict is None:
        if outline.entities:
            unchecked.append(view)
        return ()
    found = [{"about": "%s, in %s" % (trouble.said, view),
              "stands": "the sheet prints it",
              "was_also_said": "the view draws it otherwise"}
             for trouble in verdict.disagrees]
    found.extend({"about": "%s, in %s" % (trouble.said, view),
                  "stands": "the solver cannot satisfy this reading"}
                 for trouble in verdict.conflicts + verdict.redundant)
    return found


def _a_freecad_of_our_own(events):
    """
    A headless FreeCAD to build the reading in, or nothing.

    Never the live one, and never this thread. `sketch_check` makes a Document,
    writes Sketcher constraints into it and solves — and `_describe_the_part`
    runs on the loop's worker thread, where a Document mutation takes FreeCAD
    down with it (`core/apply.py`, FCC-126). One warm child for the whole
    sweep: four sheets are 142 outlines, and a process each is a process each.

    Nothing to start is not an error, and the caller says so on the transcript:
    a machine with no headless FreeCAD reads its drawing exactly as it does
    today, but a check that ran on no outline at all is not allowed to look
    like a check that found nothing.
    """
    try:
        worker = kernel.Kernel()
        worker.start()
        return worker
    except Exception as exc:
        events.emit(ERROR, message="the headless FreeCAD could not start: "
                                    "%s: %s" % (type(exc).__name__, exc))
        return None


def _round_evidence(attachments):
    """What every attached sheet was traced curved at, view by view."""
    found = {}
    for path in attachments:
        found.update(sheet_reader.round_evidence(sheet_reader.read(path)))
    return found


