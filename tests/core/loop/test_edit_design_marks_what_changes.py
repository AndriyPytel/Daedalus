"""On an edit every feature carries `edit: changed | kept`, and a Design that changes nothing is refused."""
from core.design.presence_checks import nothing_changed
from core.agent.phase_contract import the_base_faces
from core.events import EventStream
from core.loop import describe as describe_module
from core.loop.budgets import Budgets
from core.loop.prompt import prompt


def _part(*marks):
    return {"features": [{"id": "feature-%d" % n, "edit": mark} for n, mark in enumerate(marks)]}


def test_a_design_with_no_changed_feature_is_a_finding():
    assert nothing_changed(_part("kept", "kept"))
    assert nothing_changed(_part())


def test_one_changed_feature_is_enough():
    assert nothing_changed(_part("kept", "changed")) == []
    assert nothing_changed(_part("Changed ")) == []


def test_a_superseded_changed_feature_does_not_count():
    part = _part("kept", "changed")
    part["features"][1]["superseded_by"] = "feature-0"

    assert nothing_changed(part)


def test_the_design_prompt_asks_for_the_marker_on_an_edit():
    said = " ".join(prompt("design").split())

    assert '"edit": "changed"' in said and '"kept"' in said


def _asked(monkeypatch, answers):
    asked = []

    def design(body, *args, **kwargs):
        asked.append(body)
        return answers.pop(0) if answers else {}, ""

    monkeypatch.setattr(describe_module, "design", design)
    return asked


def test_an_edit_whose_design_changes_nothing_is_asked_again_and_then_refused(monkeypatch):
    asked = _asked(monkeypatch, [])

    refused = describe_module._the_change_is_marked(
        _part("kept"), "lengthen the flanges", [], None, EventStream(), Budgets(), lambda: False,
        base="plane z = 10")

    assert len(asked) == Budgets().rereads
    assert any("edit" in finding["about"] for finding in refused["conflicts"])


def test_the_second_asking_that_marks_a_feature_changed_settles_it(monkeypatch):
    asked = _asked(monkeypatch, [{"features": [{"id": "feature-0", "edit": "changed", "at": ["plane z = 10"]}]}])

    with the_base_faces({"plane z = 10": 1.0}):
        settled = describe_module._the_change_is_marked(
            {"features": [{"id": "feature-0"}]}, "lengthen the flanges", [], None, EventStream(),
            Budgets(), lambda: False, base="planes:\n  plane z = 10 (area 1 mm2)")

    assert len(asked) == 1 and not settled.get("conflicts")


def test_a_run_without_a_base_part_is_not_asked(monkeypatch):
    asked = _asked(monkeypatch, [])

    describe_module._the_change_is_marked(
        _part("kept"), "a box", [], None, EventStream(), Budgets(), lambda: False, base=None)

    assert asked == []
