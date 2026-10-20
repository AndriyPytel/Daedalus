"""The Design phase repeats a page-sized group's own reading, not the sheet
extraction underneath it, and merges every reading through the same
grow-only merge so a misread digit becomes a conflict instead of a value."""
import contextlib

import pytest

import core.loop.describe as loop_describe
import core.loop.design as loop_design
from core.loop.budgets import Budgets
from core.loop.design import _asked_about_a_group
from core.nobody import nobody_answering


def _scripted(replies):
    calls = []

    def fake_design(body, llm, events, attachments=(), views=None, phase=None,
                    keep_printed=False, pictures=True, supervisor=None, kernel=None):
        calls.append(views)
        return replies[len(calls) - 1]

    return calls, fake_design


def test_a_group_is_read_readings_times_and_merged(monkeypatch):
    calls, fake_design = _scripted([
        ({"envelope": {"length": {"value": "100 mm", "flag": "read"}}}, ""),
        ({"envelope": {"length": {"value": "108 mm", "flag": "read"}}}, ""),
        ({"envelope": {"length": {"value": "100 mm", "flag": "read"}}}, ""),
    ])
    monkeypatch.setattr(loop_design, "design", fake_design)

    grown, question = _asked_about_a_group("page 1", ["top view"], {}, object(),
                                           object(), [], readings=3)

    assert len(calls) == 3, "the group's own reading did not run readings times"
    assert calls == [["top view"]] * 3, "every reading looked at the same views"
    assert question == ""
    length = grown["envelope"]["length"]
    assert length["value"] == "100 mm", "the first reading stands, as merge always holds"
    conflict = grown["conflicts"][0]
    assert conflict["stands"] == {"value": "100 mm", "flag": "read"}
    assert conflict["was_also_said"] == {"value": "108 mm", "flag": "read"}


def test_three_identical_readings_produce_no_conflict(monkeypatch):
    reply = {"envelope": {"length": {"value": "100 mm", "flag": "read"}}}
    calls, fake_design = _scripted([(reply, ""), (reply, ""), (reply, "")])
    monkeypatch.setattr(loop_design, "design", fake_design)

    grown, _ = _asked_about_a_group("page 1", ["top view"], {}, object(),
                                    object(), [], readings=3)

    assert len(calls) == 3
    assert grown.get("conflicts") is None, \
        "three readings that agree must not grow the conflicts list"
    assert grown["envelope"]["length"]["value"] == "100 mm"


@pytest.mark.parametrize("nobody, readings", [(True, 1), (False, 3)])
def test_a_group_is_read_once_when_nobody_answers_and_three_times_when_a_person_does(
        monkeypatch, nobody, readings):
    seen = []

    def stop_after_noting(intent, llm, events, attachments, cancelled, how_many, *rest):
        seen.append(how_many)
        raise RuntimeError("noted")

    monkeypatch.setattr(loop_describe, "_first_pass", stop_after_noting)

    with (nobody_answering() if nobody else contextlib.nullcontext()), pytest.raises(RuntimeError):
        loop_describe._describe_the_part("a part", object(), object(), Budgets(), None,
                                         lambda: False, [])

    assert seen == [readings]
