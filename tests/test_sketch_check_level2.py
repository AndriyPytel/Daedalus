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
