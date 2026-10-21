"""The Design phase repeats a page-sized group's own reading, not the sheet
extraction underneath it, and merges every reading through the same
grow-only merge so a misread digit becomes a conflict instead of a value."""
import itertools

import pytest

import core.loop.describe as loop_describe
import core.loop.design as loop_design
from core.loop.budgets import Budgets
from core.loop.design import _asked_about_a_group
from core.nobody import nobody_answering


def _scripted(replies):
    calls = []
    turns = itertools.count()

    def fake_design(body, llm, events, attachments=(), views=None, phase=None,
                    keep_printed=False, pictures=True, supervisor=None, kernel=None):
        at = next(turns)
        calls.append(views)
        return replies[at]

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


def test_a_run_reads_each_group_three_times_by_default():
    assert Budgets().readings is None
    seen = []

    def stop_after_noting(intent, llm, events, attachments, cancelled, how_many, *rest):
        seen.append(how_many)
        raise RuntimeError("noted")

    original = loop_describe._first_pass
    loop_describe._first_pass = stop_after_noting
    try:
        with pytest.raises(RuntimeError):
            loop_describe._describe_the_part("a part", object(), object(), Budgets(), None,
                                             lambda: False, [])
    finally:
        loop_describe._first_pass = original

    assert seen == [3]


def test_where_nobody_answers_the_readings_run_together_and_the_one_with_most_printed_numbers_stands(
        monkeypatch):
    def printed(*callouts):
        return {"printed_on_the_sheet": [{"callout": c} for c in callouts],
                "envelope": {"length": {"value": "%s mm" % len(callouts), "flag": "read"}}}

    calls, fake_design = _scripted([(printed("1"), ""), (printed("1", "2", "3"), "a?"),
                                    (printed("1", "2"), "")])
    monkeypatch.setattr(loop_design, "design", fake_design)

    with nobody_answering():
        grown, question = _asked_about_a_group("page 1", ["top view"], {}, object(),
                                               object(), [], readings=3)

    assert len(calls) == 3
    assert [c["callout"] for c in grown["printed_on_the_sheet"]] == ["1", "2", "3"]
    assert grown["envelope"]["length"]["value"] == "3 mm"
    assert "conflicts" not in grown
    assert question == "a?"
