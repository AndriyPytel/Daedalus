"""
Level 2 — the reading, built, and what the solver says about it.

Needs FreeCAD: everything here is a real `Sketcher::SketchObject` in a real
document, because the whole point of the module is that a solver has an opinion
of its own and nothing else in the pipeline does.
"""
import os
from dataclasses import replace

import pytest

from core import sheet_reader, sketch_check

pytestmark = pytest.mark.freecad

SHEET = os.path.join(os.path.dirname(__file__), "modelmania", "mm25-p1-1.pdf")


@pytest.fixture(scope="module")
def sheet(sheet_cache):
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return sheet_cache(SHEET)


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
    The floor: what the pixels were measured into can be built all at once, and
    the sheet's own numbers keep the movement below the reading threshold. The
    four-sheet evidence test holds the aggregate movement bands.
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
    and the verdict counts how many sizes came from the sheet's own text. On
    `mm25-p1-1.pdf` that count is zero everywhere: the tracing reads a
    millimetre under what the sheet prints, which is outside the snap band, so
    every size here is the pixels' own and the verdict says so. What a carried
    size counts as is said where one exists, over the bore in two pieces.
    """
    verdicts = [sketch_check.checked(outline, view)
                for view, outline in _outlines(sheet)]

    assert verdicts
    assert all(verdict.printed == 0 for verdict in verdicts)
    assert all(verdict.entities for verdict in verdicts)


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
    The sheet agrees with itself, so none of its outline verdicts is reported.
    The four-sheet evidence test holds the aggregate distance and disagreement
    measurements.
    """
    for view, outline in _outlines(sheet):
        verdict = sketch_check.checked(outline, view, sheet.between)

        assert verdict.disagrees == [], (view, verdict.disagrees)


def _a_bracket():
    """
    A C traced as a closed walk: two faces of it are collinear and 30 mm apart.

    What a rectangle cannot show — a foot standing on one face of a pair that
    share a line, where the other is the wrong answer at the same distance
    across the dimension.
    """
    corners = [(0.0, 0.0), (50.0, 0.0), (50.0, 10.0), (10.0, 10.0),
               (10.0, 40.0), (50.0, 40.0), (50.0, 50.0), (0.0, 50.0)]
    return sheet_reader.Outline(
        entities=[sheet_reader.Entity(
            kind="line", source="traced", role="contour",
            size=sheet_reader._distance(corners[at],
                                        corners[(at + 1) % len(corners)]),
            start=corners[at], end=corners[(at + 1) % len(corners)])
            for at in range(len(corners))],
        closed=True, area=1400.0)


def test_a_conflicting_constraint_comes_back_at_the_entity_it_was_written_for():
    """
    Two printed distances between the same two faces that cannot both be true,
    put through the real solver. What the solver hands back is its own
    constraint numbering, one-based; what a `Trouble` says is where in the walk
    the trouble is, and that is the numbering every other Trouble in the
    verdict uses. The two are separate fields because they are separate
    coordinate systems: here the walk has four entities and the sketch has six
    constraints, four coincidences and then the two distances.
    """
    plate = _a_plate(50.0, 30.0)
    between = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                    first=(0.0, 15.0), second=(50.0, 15.0)),
               sheet_reader.Between(printed=60.0, text="60", view="a view",
                                    first=(0.0, 10.0), second=(50.0, 10.0))]

    verdict = sketch_check.checked(plate, "a view", between)

    assert verdict.conflicts
    assert {trouble.constraint for trouble in verdict.conflicts} == {4, 5}
    for trouble in verdict.conflicts:
        assert trouble.entity is plate.entities[trouble.at], trouble.said
        assert trouble.printed in (50.0, 60.0), trouble.said
        assert "the sheet prints" in trouble.said


def test_a_foot_that_reaches_no_face_of_this_outline_writes_nothing():
    """
    Most feet reach nothing here: the view was not traced, or the face was not.

    Off across the dimension and off along the face are both misses, and the
    second is the one a line rather than a segment cannot tell: a foot 120 mm
    above the plate stands on the left face's own infinite line and on no part
    of the face.
    """
    plate = _a_plate(50.0, 30.0)
    across = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                   first=(0.0, 15.0), second=(90.0, 15.0))]
    beyond = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                   first=(0.0, 120.0), second=(50.0, 120.0))]

    assert sketch_check.checked(plate, "a view", across).distances == 0
    assert sketch_check.checked(plate, "a view", beyond).distances == 0


def test_a_collinear_face_elsewhere_in_the_view_is_not_the_face_measured_to():
    """
    Two faces of the same walk on one line, and a foot on the far one: the
    distance across the dimension is zero to both, so the only thing that tells
    them apart is how far along their own direction the foot stands.
    """
    bracket = _a_bracket()

    assert sketch_check._the_face_at(bracket, (50.0, 45.0), (1.0, 0.0)) == 5
    assert sketch_check._the_face_at(bracket, (50.0, 5.0), (1.0, 0.0)) == 1


def test_a_walk_of_lines_says_nothing_until_a_printed_distance_reaches_it():
    """
    Why the distances had to be written at all: coincidences are satisfied
    where the pixels already put the walk, so `solved == 0` and `moved == 0` on
    an outline of lines are the constraints being true by construction and not
    a verdict about the reading. The same plate, traced 48 mm wide against a
    sheet that prints 50, moves 1.08 mm and says which dimension moved it.
    """
    plate = _a_plate(48.0, 30.0)
    between = [sheet_reader.Between(printed=50.0, text="50", view="a view",
                                    first=(0.0, 15.0), second=(48.0, 15.0))]

    assert sketch_check.checked(plate, "a view").moved == 0.0

    verdict = sketch_check.checked(plate, "a view", between)

    assert verdict.distances == 1
    assert verdict.moved > 1.0, verdict.moved
    assert verdict.disagrees


def _as_it_crosses(verdict):
    """
    A verdict with the two differences a process boundary is entitled to.

    `entity` is the caller's own traced object, which does not cross the pipe by
    design and which nothing upstream reads. `moved` is a float the solver
    settles differently run to run from a start it picks for itself — 0.40 mm
    apart at the widest over the four sheets, which is why it is asserted as a
    band beside this rather than compared here.

    Every other field is compared as it stands, whichever it is: a field added
    to `Verdict` or to `Trouble` later is covered without anyone coming back
    here, and that is the whole point of comparing whole verdicts.
    """
    said = dict(vars(verdict))
    for named, value in said.items():
        if isinstance(value, list):
            said[named] = [replace(trouble, entity=None) for trouble in value]
    said["moved"] = 0.0
    return sketch_check.Verdict(**said)


def test_the_child_answers_the_same_thing_this_process_does(modelmania_sheets):
    """
    The move that keeps FreeCAD open has to cost the verdict nothing. Every
    outline of all four sheets — 142 of them when this was measured — built once
    here and once in the Preflight child, and the two verdicts compared whole
    rather than over a chosen tuple of fields.

    Whole because the wire form crosses by field list: an attribute it forgets
    is invisible to every field a chosen tuple does compare, and `conflicts` and
    `redundant` are what the description is written from.
    """
    from core.preflight import PreflightWorker

    compared = 0
    with PreflightWorker() as worker:
        for sheet in modelmania_sheets.values():
            for view, outline in _outlines(sheet):
                here = sketch_check.checked(outline, view, sheet.between)
                there = sketch_check.checked_by(worker, outline, view,
                                                sheet.between)

                assert there is not None, view
                assert _as_it_crosses(there) == _as_it_crosses(here), view
                assert abs(there.moved - here.moved) < sketch_check.A_SHIFT / 2, view
                compared += 1

    assert compared > 100, compared


def test_a_conflict_and_a_redundancy_reach_the_caller_from_the_child():
    """
    The four sheets raise neither, so the two fields the description conflicts
    are written from are exercised on readings built to raise them: a plate the
    sheet dimensions 50 and 60 across the same two faces, which cannot hold
    both, and a bore traced as two arcs the sheet prints Ø42 for twice, which
    is told its size once more than it needs.

    Each arrives back at the entity of the walk it was written for and with the
    number it was written from, and the verdict crossing the pipe is the verdict
    this process builds.
    """
    from core.preflight import PreflightWorker

    plate = _a_plate(50.0, 30.0)
    contradicting = [
        sheet_reader.Between(printed=50.0, text="50", view="a view",
                             first=(0.0, 5.0), second=(50.0, 5.0)),
        sheet_reader.Between(printed=60.0, text="60", view="a view",
                             first=(0.0, 25.0), second=(50.0, 25.0))]
    bore = _a_bore_in_two_pieces(42.0, 42.0)

    with PreflightWorker() as worker:
        conflicted = sketch_check.checked_by(worker, plate, "a view", contradicting)
        repeated = sketch_check.checked_by(worker, bore, "a view")

    assert [(trouble.at, trouble.printed) for trouble in conflicted.conflicts] == \
           [(3, 50.0), (3, 60.0)]
    assert [(trouble.at, trouble.printed) for trouble in repeated.redundant] == \
           [(1, 42.0)]
    assert _as_it_crosses(conflicted) == _as_it_crosses(
        sketch_check.checked(plate, "a view", contradicting))
    assert _as_it_crosses(repeated) == _as_it_crosses(
        sketch_check.checked(bore, "a view"))


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
