"""An unsettled pair -- one feature described twice, with values that differ -- reaches Assertion as no check at all."""
from core.design.gaps import entries
from core.events import ASSERTION
from core.loop.plan import _acceptance_of, _left_out, _unconsumed

from tests.event_helpers import events_of
from tests.loop_helpers import (RENDER_PNG, _grown, assertion_reply, code_reply, design_reply, drive, everything,
                                plan_reply, read, suspect_reply, verdict_reply)


def _boss(identity, diameter, front, kind="boss"):
    return {"id": identity, "kind": kind, "diameter": read(diameter),
            "position": {"the front face": read(front)}}


def _described(*features):
    return _grown([{"datums": [{"name": "the front face", "is": "the flat front face",
                                "fixes": "the depth front to rear",
                                "proves": "40 mm to the rear face"}],
                    "envelope": {"length": read("80 mm")},
                    "features": list(features)}])


def _about(the_part, identity):
    return [line for line in _acceptance_of(the_part) if "features \u2192 %s " % identity in line + " "
            or "features \u2192 %s \u2192" % identity in line]


def test_a_pair_described_twice_with_different_values_gives_no_entry_for_either_feature():
    the_part = _described(_boss("boss-centre", "110 mm", "10 mm"),
                          _boss("boss-left", "100 mm", "10 mm"),
                          _boss("hole-1", "6 mm", "30 mm", kind="hole"))

    acceptance = _acceptance_of(the_part)

    assert _about(the_part, "boss-centre") == [] and _about(the_part, "boss-left") == []
    assert any("hole-1" in line for line in acceptance)
    assert any(line.startswith("envelope") for line in acceptance)


def test_the_reason_names_each_feature_left_out_and_the_values_that_differed():
    the_part = _described(_boss("boss-centre", "110 mm", "10 mm"),
                          _boss("boss-left", "100 mm", "10 mm"))

    (reason,) = _left_out(the_part)

    assert "boss-centre" in reason and "boss-left" in reason
    assert "110 mm" in reason and "100 mm" in reason


def test_a_pair_described_twice_with_the_same_values_is_still_checked():
    the_part = _described(_boss("boss-a", "100 mm", "10 mm"), _boss("boss-b", "100 mm", "10 mm"))

    assert _about(the_part, "boss-a") and _about(the_part, "boss-b")
    assert _left_out(the_part) == []


def test_an_ordinary_conflict_leaves_nothing_out():
    the_part = _described(_boss("boss-a", "100 mm", "10 mm"), _boss("hole-1", "6 mm", "30 mm", kind="hole"))
    the_part["conflicts"] = [{"about": "features \u2192 boss-a \u2192 diameter",
                              "stands": "100 mm", "was_also_said": "100.0 mm"}]

    assert _about(the_part, "boss-a")
    assert _left_out(the_part) == []


def test_two_placements_of_one_axis_that_disagree_on_a_shared_datum_are_left_out_too():
    def _on_axis(identity, kind, front, top):
        return {"id": identity, "kind": kind, "axis": read("parallel to the depth front to rear"),
                "diameter": read("%s mm" % (front + 4)),
                "position": {"the front face": read("%s mm" % front), "the top face": read("%s mm" % top)}}

    the_part = _described(_on_axis("pin-a", "hole", 10, 20), _on_axis("pin-b", "slot", 10, 25))

    assert _about(the_part, "pin-a") == [] and _about(the_part, "pin-b") == []


def test_plan_coverage_and_the_enumeration_still_see_both_readings_of_the_pair():
    the_part = _described(_boss("boss-centre", "110 mm", "10 mm"), _boss("boss-left", "100 mm", "10 mm"))

    every = entries(the_part)
    wanted = _unconsumed(the_part, [])

    for identity in ("boss-centre", "boss-left"):
        assert any(identity in entry["about"] for entry in every)
        assert any(identity in about for about in wanted)


PAIR = {"envelope": {"length": read("80 mm")},
        "datums": [{"name": "the front face", "is": "the flat front face", "fixes": "the depth front to rear",
                    "proves": "40 mm to the rear face"}],
        "features": [{"id": "boss-centre", "kind": "boss", "diameter": read("110 mm"),
                      "position": {"the front face": read("10 mm")}},
                     {"id": "boss-left", "kind": "boss", "diameter": read("100 mm"),
                      "position": {"the front face": read("10 mm")}}]}


def test_the_assertion_report_names_each_feature_left_out_of_the_checks():
    _, events, _, _ = drive([
        design_reply(PAIR),
        plan_reply("Only", satisfies=everything(PAIR)),
        code_reply("box()"),
        suspect_reply(),
        assertion_reply(),
        verdict_reply("pass"),
    ], attachments=[RENDER_PNG])

    reported = [event.data["left_out"] for event in events_of(events, ASSERTION) if event.data.get("left_out")]

    assert reported
    assert "boss-centre" in reported[-1][0] and "boss-left" in reported[-1][0]
