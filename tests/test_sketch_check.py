"""
Level 1 — the reading on its way to the child and its verdict on the way back.

Nothing here needs a FreeCAD. What is checked is the crossing itself: a traced
outline is a walk of dataclasses in this process, JSON in the pipe, and plain
objects with the same attribute names in the child — and a point that arrives
as a list rather than a tuple breaks the reporting rather than the geometry,
silently, in a `%` format string (FCC-126).
"""
import json

from core import sheet_reader, sketch_check


def _a_plate(printed=None):
    corners = [(0.0, 0.0), (40.0, 0.0), (40.0, 20.0), (0.0, 20.0)]
    entities = [
        sheet_reader.Entity(kind="line", source="traced", role="contour",
                            size=40.0, start=corners[at],
                            end=corners[(at + 1) % 4])
        for at in range(4)]
    entities.append(sheet_reader.Entity(
        kind="circle", source="traced", role="contour", size=6.0,
        centre=(20.0, 10.0), diameter=6.0, printed=printed))
    entities.append(sheet_reader.Entity(
        kind="arc", source="traced", role="contour", size=3.0,
        centre=(5.0, 5.0), radius=3.0, start=(8.0, 5.0), end=(2.0, 5.0)))
    return sheet_reader.Outline(entities=entities, closed=True, area=800.0)


def _a_distance():
    return sheet_reader.Between(printed=40.0, text="40", view="the front view",
                                first=(0.0, 10.0), second=(40.0, 10.0))


def test_the_outline_survives_the_pipe_intact():
    outline = _a_plate(printed=6.5)
    request = sketch_check.as_request(outline, "the front view", [_a_distance()])

    there, view, between = sketch_check.from_request(
        json.loads(json.dumps(request)))

    assert view == "the front view"
    assert there.closed is True
    assert [e.kind for e in there.entities] == [e.kind for e in outline.entities]
    assert there.entities[4].printed == 6.5
    assert there.entities[5].radius == 3.0
    assert [b.printed for b in between] == [40.0]


def test_every_point_arrives_as_a_tuple():
    """
    JSON has no tuples, and the reporting formats points with `%`: a list there
    raises `TypeError: not enough arguments` from inside a docstringless helper
    and takes the verdict with it.
    """
    outline, _, between = sketch_check.from_request(json.loads(json.dumps(
        sketch_check.as_request(_a_plate(), "a view", [_a_distance()]))))

    assert isinstance(outline.entities[0].start, tuple)
    assert isinstance(outline.entities[0].end, tuple)
    assert isinstance(outline.entities[4].centre, tuple)
    assert isinstance(between[0].first, tuple)
    assert "(0.00, 10.00)" == sketch_check._spelt(between[0].first)


def test_the_verdict_comes_back_as_the_verdict():
    verdict = sketch_check.Verdict(
        view="the front view", entities=6, geometry=6, solved=0, dof=4,
        printed=1, distances=1,
        disagrees=[sketch_check.Trouble(at=2, constraint=7, printed=40.0,
                                        said="prints 40")],
        moved=9.88,
        shifted=[sketch_check.Trouble(at=0, printed=42.0, said="moved 9.88 mm")])

    back = sketch_check.from_response(
        json.loads(json.dumps(sketch_check.as_response(verdict))))

    assert back.view == "the front view"
    assert back.moved == 9.88
    assert [t.said for t in back.disagrees] == ["prints 40"]
    assert [(t.at, t.constraint) for t in back.disagrees] == [(2, 7)]
    assert [t.printed for t in back.shifted] == [42.0]


def test_a_child_that_could_not_answer_is_no_verdict():
    """
    A dead or hung child is a reading nobody checked, not a reading that
    passed. The loop reads None as "nothing to say about this outline".
    """
    assert sketch_check.from_response(None) is None
    assert sketch_check.from_response({"ok": True, "verdict": None}) is None


def test_a_reading_the_solver_gave_up_on_is_built_again(monkeypatch):
    """
    A reading is under-constrained on purpose, and from a start the solver
    picks for itself the same sketch comes out solved or not solved run to run
    — three builds in two hundred, measured on a bore given two printed sizes.
    A build it gave up on leaves the geometry where the pixels put it, which is
    what a reading with nothing wrong with it looks like, so the verdict has to
    come off a solve that converged. FCC-127.
    """
    built = []

    def _built(outline, view, between):
        built.append(view)
        return sketch_check.Verdict(view=view, solved=0 if len(built) > 1 else -3,
                                    moved=9.88 if len(built) > 1 else 0.0)

    monkeypatch.setattr(sketch_check, "available", lambda: True)
    monkeypatch.setattr(sketch_check, "_built", _built)
    verdict = sketch_check.checked(_a_plate(), "the front view")

    assert len(built) == 2, "the verdict was read off a solve that gave up"
    assert verdict.solved == 0
    assert verdict.moved == 9.88


def test_a_reading_nothing_can_solve_is_not_built_for_ever(monkeypatch):
    """A fourth document does not make an unsolvable reading solvable."""
    built = []

    def _built(outline, view, between):
        built.append(view)
        return sketch_check.Verdict(view=view, solved=-3)

    monkeypatch.setattr(sketch_check, "available", lambda: True)
    monkeypatch.setattr(sketch_check, "_built", _built)
    verdict = sketch_check.checked(_a_plate(), "the front view")

    assert len(built) == sketch_check._TRIES
    assert verdict.solved == -3, "a reading nobody could solve came back as solved"


def test_a_reading_that_solves_first_time_is_built_once(monkeypatch):
    built = []

    def _built(outline, view, between):
        built.append(view)
        return sketch_check.Verdict(view=view, solved=0)

    monkeypatch.setattr(sketch_check, "available", lambda: True)
    monkeypatch.setattr(sketch_check, "_built", _built)
    sketch_check.checked(_a_plate(), "the front view")

    assert len(built) == 1


def test_an_empty_outline_never_reaches_the_child():
    asked = []

    class _Worker:
        def reading(self, request, timeout=None):
            asked.append(request)
            return None

    assert sketch_check.checked_by(_Worker(), sheet_reader.Outline()) is None
    assert asked == []
