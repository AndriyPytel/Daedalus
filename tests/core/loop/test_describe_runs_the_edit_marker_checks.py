"""The edit marker checks run on the path `run()` takes to describe an edit: an `at` that is no address is asked again and then refused."""
from core.agent.phase_contract import the_base_faces
from core.events import DESIGN, EventStream
from core.loop import describe as describe_module
from core.loop import design as design_module
from core.loop.budgets import Budgets

BASE = "planes:\n  plane y = -17.5 (area 1605.35 mm2)"
FACES = {"plane y = -17.5": 1605.35}


def _part(at):
    return {"envelope": {"length": {"value": "10 mm", "flag": "read"}},
            "features": [{"id": "boss-centre", "edit": "changed", "at": at}]}


def _described(monkeypatch, answers):
    asked = []

    def design(body, *args, **kwargs):
        asked.append(body)
        return (answers.pop(0) if answers else {}), ""

    monkeypatch.setattr(describe_module, "design", design)
    monkeypatch.setattr(design_module, "design", design)
    events = EventStream()
    with the_base_faces(FACES):
        describe_module._describe_the_part(
            "move the boss", None, events, Budgets(), None, lambda: False, [], base=BASE)
    return events.last(DESIGN).data["model"], asked


def _questions(asked):
    return [body["the_question"] for body in asked if "at" in str(body.get("the_question", ""))
            and "the_base_part" in str(body.get("the_question", ""))]


def test_an_at_that_is_no_address_is_asked_again_and_then_refused(monkeypatch):
    part, asked = _described(monkeypatch, [_part(["the bore axis"])])

    assert len(_questions(asked)) == Budgets().rereads
    assert any("at" in str(finding.get("about")) for finding in part["conflicts"])


def test_an_at_the_base_part_lists_is_not_asked_about(monkeypatch):
    part, asked = _described(monkeypatch, [_part(["plane y = -17.5"])])

    assert _questions(asked) == []
    assert not any("at" in str(finding.get("about")) for finding in part.get("conflicts") or ())


def test_the_second_asking_that_gives_an_address_settles_it(monkeypatch):
    fixed = {"features": [{"id": "boss-centre", "at": ["plane y = -17.5"]}]}
    part, asked = _described(monkeypatch, [_part([]), fixed])

    assert len(_questions(asked)) == 1
    assert not any("at" in str(finding.get("about")) for finding in part.get("conflicts") or ())


def test_a_run_without_a_base_part_is_not_asked(monkeypatch):
    asked = []
    def design(body, *a, **k):
        asked.append(body)
        return _part([]), ""

    monkeypatch.setattr(describe_module, "design", design)
    monkeypatch.setattr(design_module, "design", design)

    describe_module._describe_the_part("a box", None, EventStream(), Budgets(), None,
                                       lambda: False, [], base=None)

    assert _questions(asked) == []
