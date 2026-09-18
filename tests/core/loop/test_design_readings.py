"""The Design phase repeats a page-sized group's own reading, not the sheet
extraction underneath it, and merges every reading through the same
grow-only merge so a misread digit becomes a conflict instead of a value."""
import core.loop.design as loop_design
from core.loop.design import _asked_about_a_group


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
