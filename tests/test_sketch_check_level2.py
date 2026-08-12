"""
Level 2 — the reading, built, and what the solver says about it.

Needs FreeCAD: everything here is a real `Sketcher::SketchObject` in a real
document, because the whole point of the module is that a solver has an opinion
of its own and nothing else in the pipeline does.
"""
import os

import pytest

from core import sheet_reader, sketch_check

pytestmark = pytest.mark.freecad

SHEET = os.path.join(os.path.dirname(__file__), "modelmania", "mm25-p1-1.pdf")
SHEET_2023 = os.path.join(os.path.dirname(__file__), "modelmania",
                          "model-mania_-in-person-2023 copy.pdf")


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return sheet_reader.read(SHEET, into=str(tmp_path_factory.mktemp("sheet")))


def _outlines(sheet):
    return [(view.view, outline) for view in sheet.drawn
            for outline in view.outlines]


def _a_bore_in_two_pieces(first, second):
    """
    The 2023 sheet's \u00d842 bore as the tracer actually gets it: not a circle but
    arcs, because the centre lines and the section hatch cross it. Each arc
    given the size of a different callout is the reading this check exists for.
    """
    import math

    def arc(printed, from_angle, to_angle):
        radius = printed / 2.0
        return sheet_reader.Entity(
            kind="arc", source="traced", role="contour", size=printed,
            radius=radius, centre=(0.0, 0.0), printed=printed,
            start=(radius * math.cos(from_angle), radius * math.sin(from_angle)),
            end=(radius * math.cos(to_angle), radius * math.sin(to_angle)))

    return sheet_reader.Outline(
        entities=[arc(first, 0.0, math.pi), arc(second, math.pi, 2 * math.pi)],
        closed=True, area=math.pi * (first / 2.0) ** 2)


def test_a_closed_walk_off_a_real_sheet_becomes_a_sketch(sheet):
    """
    Every entity arrives as geometry, and the walk arrives as the coincidences
    that say these two edges meet. Nothing is dropped in the crossing.
    """
    built = [(view, sketch_check.checked(outline, view))
             for view, outline in _outlines(sheet)]

    assert built
    for view, verdict in built:
        assert verdict is not None, view
        assert verdict.geometry == verdict.entities, view


def test_the_tracing_does_not_contradict_itself(sheet):
    """
    The floor, and it was not known before: what the pixels were measured into
    can be built all at once, and the sheet's own numbers cost it under a
    millimetre and a half to satisfy. Measured over all four sheets, 96
    outlines: median 0.27 mm, nine in ten under 0.62, worst 1.21 — the slack in
    a fit through pixels 0.2 mm across.
    """
    for view, outline in _outlines(sheet):
        verdict = sketch_check.checked(outline, view)

        assert verdict.solved == 0, (view, verdict.solved)
        assert verdict.conflicts == [], (view, verdict.conflicts)
        assert verdict.moved < sketch_check.A_SHIFT, (view, verdict.moved)
        assert verdict.shifted == [], (view, verdict.shifted)


def test_a_profile_off_one_view_is_not_expected_to_be_determined(sheet):
    """
    Degrees of freedom are reported and never required. One view carries no
    position, and a check that demanded zero here would be demanding that the
    module invent one.
    """
    verdicts = [sketch_check.checked(outline, view)
                for view, outline in _outlines(sheet)]

    assert all(verdict.dof > 0 for verdict in verdicts)


def test_one_bore_given_two_printed_sizes_moves_the_geometry_that_was_measured():
    """
    What the check is for, and why the solver's return code is not the answer:
    two numbers that cannot both be true are usually *solvable* — the solver
    satisfies them by moving the geometry, and the geometry is the one thing
    here that was measured. Two arcs of one bore, one snapped to 42 and one to
    50, cost 9.88 mm of movement, which is the size of the error ADR-0019 lost.
    """
    verdict = sketch_check.checked(_a_bore_in_two_pieces(42.0, 50.0), "a view")

    assert verdict.moved > 5.0, verdict.moved
    assert verdict.shifted
    assert {trouble.printed for trouble in verdict.shifted} <= {42.0, 50.0}
    assert any("printed on the sheet" in trouble.said
               for trouble in verdict.shifted)


def test_the_same_bore_read_the_same_way_twice_costs_nothing():
    """The check is silent where the sheet agrees with itself."""
    verdict = sketch_check.checked(_a_bore_in_two_pieces(42.0, 42.0), "a view")

    assert verdict.moved == 0.0
    assert verdict.shifted == []
    assert verdict.printed == 2


def test_a_size_nobody_printed_is_written_from_the_pixels_and_says_so(sheet):
    """
    Where no callout reached a hole the traced measurement is what there is,
    and the verdict counts how many sizes came from the sheet's own text.
    """
    verdicts = [sketch_check.checked(outline, view)
                for view, outline in _outlines(sheet)]

    assert any(verdict.printed for verdict in verdicts), (
        "one callout on this sheet reaches a traced hole")
    assert any(verdict.printed == 0 for verdict in verdicts)


def test_nothing_is_left_in_the_user_s_own_documents(sheet):
    """Disposable, and the document is closed even where the build fails."""
    import FreeCAD

    before = set(FreeCAD.listDocuments())

    for view, outline in _outlines(sheet):
        sketch_check.checked(outline, view)

    assert set(FreeCAD.listDocuments()) == before


def _a_plate(wide, tall):
    """A traced rectangle, walked round, as the tracer hands one over."""
    corners = [(0.0, 0.0), (wide, 0.0), (wide, tall), (0.0, tall)]
    entities = [
        sheet_reader.Entity(kind="line", source="traced", role="contour",
                            size=sheet_reader._distance(corners[at],
                                                        corners[(at + 1) % 4]),
                            start=corners[at], end=corners[(at + 1) % 4])
        for at in range(4)]
    return sheet_reader.Outline(entities=entities, closed=True,
                                area=wide * tall)


def test_a_printed_distance_reaches_two_faces_of_the_outline(sheet):
    """
    The drawing arriving in the sketch. A diameter arrives where a leader
    reached a hole — once on four sheets — and everything else a drawing says
    is a distance.
    """
    carried = [sketch_check.checked(outline, view, sheet.between)
               for view, outline in _outlines(sheet)]

    assert sum(verdict.distances for verdict in carried), (
        "this sheet dimensions its own views")


def test_a_face_traced_where_the_sheet_does_not_put_it_is_a_shift():
    """
    What the check is for, in the shape the defect actually comes in: a
    distance, not a diameter. A plate traced 48 mm wide against a sheet that
    prints 50 moves 2 mm, and the shift names the dimension that moved it.
    """
    plate = _a_plate(48.0, 30.0)
    between = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                    first=(0.0, 15.0), second=(48.0, 15.0))]

    verdict = sketch_check.checked(plate, "a view", between)

    assert verdict.distances == 1
    assert verdict.disagrees
    assert "48.00" in verdict.disagrees[0].said, verdict.disagrees[0].said
    assert verdict.disagrees[0].printed == 50.0


def test_a_plate_traced_where_the_sheet_puts_it_costs_nothing():
    """The check is silent where the tracing and the drawing agree."""
    plate = _a_plate(50.0, 30.0)
    between = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                    first=(0.0, 15.0), second=(50.0, 15.0))]

    verdict = sketch_check.checked(plate, "a view", between)

    assert verdict.distances == 1
    assert verdict.moved < 0.01, verdict.moved
    assert verdict.shifted == []
    assert verdict.disagrees == []


def test_a_dimension_drawn_across_the_sheet_is_not_written():
    """
    A diagonal dimension measures between two points, and a foot on a face
    cannot say which — so it is left out rather than guessed at.
    """
    plate = _a_plate(50.0, 30.0)
    between = [sheet_reader.Between(printed=58.3, text="58.3", view="a view",
                                    first=(0.0, 0.0), second=(50.0, 30.0))]

    assert sketch_check.checked(plate, "a view", between).distances == 0


def test_a_foot_that_reaches_no_face_of_this_outline_writes_nothing():
    """Most feet reach nothing here: the view was not traced, or the face was not."""
    plate = _a_plate(50.0, 30.0)
    between = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                    first=(0.0, 15.0), second=(90.0, 15.0))]

    assert sketch_check.checked(plate, "a view", between).distances == 0


def test_the_distances_of_another_view_are_not_written_into_this_one(sheet):
    """A view's own millimetres are its own: the same numbers name other places."""
    for view, outline in _outlines(sheet):
        mine = sketch_check.checked(outline, view, sheet.between)
        theirs = sketch_check.checked(
            outline, view, [entry for entry in sheet.between
                            if entry.view != view])

        assert theirs.distances == 0, view
        assert mine.distances >= theirs.distances


def test_a_disagreement_is_reported_and_never_resolved(sheet):
    """
    Measured on the four sheets: the 13 printed distances that reach a traced
    outline sit 0.07 to 1.08 mm from what the pixels put between the same two
    faces, and the 1.08 is the bias itself — a face is traced to the inside of
    the line that draws it. So the sheets agree with themselves, and nothing
    here is reported against them.
    """
    for view, outline in _outlines(sheet):
        verdict = sketch_check.checked(outline, view, sheet.between)

        assert verdict.disagrees == [], (view, verdict.disagrees)


# ── through the child, which is where it actually runs (FCC-126) ──────────────


def test_the_child_answers_the_same_thing_this_process_does(sheet):
    """
    The move that keeps FreeCAD open has to cost the verdict nothing. Same
    outlines, same sheet, one built here and one built in the Preflight child,
    and the answer is the answer either way.
    """
    from core.preflight import PreflightWorker

    with PreflightWorker() as worker:
        for view, outline in _outlines(sheet)[:4]:
            here = sketch_check.checked(outline, view, sheet.between)
            there = sketch_check.checked_by(worker, outline, view, sheet.between)

            assert there is not None, view
            assert (there.entities, there.geometry, there.solved, there.dof,
                    there.distances) == (here.entities, here.geometry,
                                         here.solved, here.dof, here.distances), view
            assert [t.said for t in there.disagrees] == \
                   [t.said for t in here.disagrees], view
            assert [t.said for t in there.shifted] == \
                   [t.said for t in here.shifted], view
            # Not the same float: the solver settles a slack fit slightly
            # differently run to run, and 0.44 against 0.57 mm on one section is
            # that, not a difference of process. What may not differ is the
            # finding, and `shifted` above is the finding.
            assert abs(there.moved - here.moved) < sketch_check.A_SHIFT / 2, view


def test_a_reading_the_solver_cannot_take_does_not_take_the_caller_with_it():
    """
    The whole arrangement, exercised: the child is fed the bore read as two
    different sizes, and whatever the solver makes of it, this process is still
    here to read the verdict.
    """
    from core.preflight import PreflightWorker

    with PreflightWorker() as worker:
        verdict = sketch_check.checked_by(
            worker, _a_bore_in_two_pieces(42.0, 50.0), "middle centre view")

        assert verdict.moved > 5.0, verdict.moved
        assert worker.alive
