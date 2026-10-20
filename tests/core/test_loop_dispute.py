"""
Level 1 -- a revision that finds an unmet acceptance check wrong disputes it with a measurement; the run ends unverified, and blocked only when the revision neither fixes nor disputes an entry.
"""
from core.events import BLOCKED, DISPUTED
from core.loop.prompt import prompt
from core.nobody import nobody_answering
from tests.event_helpers import first_event
from tests.loop_helpers import (
    code_reply, drive, plan_reply, suspect_reply, verdict_reply,
)

THROUGH = "features \u2192 bore \u2192 through-count: 1"
WIDTH = "envelope \u2192 width: 40 mm"


def _failing(*entries):
    return [{"ok": False,
             "traceback": "AssertionError: these did not hold: " + "; ".join(entries)}]


def _dispute(entry, measured="the bore passes through 1 face pair, count 2 is the check's error",
             because="the check counts a face per side"):
    return {"entry": entry, "measured": measured, "because": because}


def _turns(*revision):
    return [plan_reply("Make it"), code_reply("make()"), suspect_reply(), *revision]


def _kinds(events):
    return [event.kind for event in events]


def test_a_revision_that_disputes_the_one_unmet_entry_ends_the_run_unverified():
    dispute = _dispute(THROUGH)
    result, events, _, _ = drive(_turns(plan_reply(disputes=[dispute])),
                                 checks=_failing(THROUGH))

    assert result.ok and not result.blocked
    assert result.verdict is None
    assert result.unmet == [THROUGH]
    assert result.disputed == [dispute]
    assert result.steps_applied == 1
    assert first_event(events, DISPUTED).data["entries"] == [THROUGH]
    assert first_event(events, DISPUTED).data["measured"] == [dispute["measured"]]
    assert first_event(events, DISPUTED).data["because"] == [dispute["because"]]
    assert BLOCKED not in _kinds(events)


def test_a_revision_that_adds_no_step_and_disputes_nothing_still_blocks():
    result, events, _, _ = drive(_turns(plan_reply()), checks=_failing(THROUGH))

    assert result.blocked and not result.ok
    assert "The revision added no Step to the 1 already built" in result.reason
    assert DISPUTED not in _kinds(events)


def test_one_entry_disputed_and_one_left_alone_blocks_naming_the_one_left_alone():
    result, events, _, _ = drive(_turns(plan_reply(disputes=[_dispute(THROUGH)])),
                                 checks=_failing(THROUGH, WIDTH))

    assert result.blocked
    assert WIDTH in result.reason.split("Neither fixed nor disputed")[1]
    assert THROUGH not in result.reason.split("Neither fixed nor disputed")[1]


def test_a_dispute_without_a_measurement_is_not_accepted():
    bare = {"entry": THROUGH, "because": "the check is wrong"}
    result, events, _, _ = drive(_turns(plan_reply(disputes=[bare])), checks=_failing(THROUGH))

    assert result.blocked
    assert DISPUTED not in _kinds(events)


def test_a_dispute_of_an_entry_that_is_not_unmet_is_not_accepted():
    result, events, _, _ = drive(_turns(plan_reply(disputes=[_dispute(WIDTH)])),
                                 checks=_failing(THROUGH))

    assert result.blocked
    assert DISPUTED not in _kinds(events)


def test_a_revision_with_steps_and_a_dispute_of_another_entry_applies_the_steps():
    result, events, _, tools = drive(
        _turns(plan_reply("Widen it", disputes=[_dispute(THROUGH)]),
               code_reply("widen()", first=2), suspect_reply(),
               verdict_reply("pass")),
        checks=_failing(THROUGH, WIDTH))

    assert result.ok and result.verdict == "pass"
    assert result.steps_applied == 2
    assert first_event(events, DISPUTED).data["entries"] == [THROUGH]
    assert BLOCKED not in _kinds(events)


def test_plan_md_tells_the_revision_it_may_dispute_in_both_modes():
    asking, headless = prompt("plan"), None
    with nobody_answering():
        headless = prompt("plan")

    for text in (asking, headless):
        assert '"disputes"' in text
        assert "measured" in text and "because" in text


def test_a_dispute_naming_the_check_by_its_short_name_matches_the_unmet_entry_the_checks_cut():
    real = "the through-features. expected 3 through features, the solid has 6."
    result, events, _, _ = drive(
        _turns(plan_reply(disputes=[_dispute("The through-features")])), checks=_failing(real))

    assert result.ok and not result.blocked
    assert result.unmet == [real]
    assert result.disputed[0]["entry"] == real
    assert first_event(events, DISPUTED).data["entries"] == [real]


def test_the_feedback_lists_each_unmet_entry_verbatim_for_the_revision_to_copy():
    from core.loop.assertion import _why_the_checks_failed

    said = _why_the_checks_failed("AssertionError: these did not hold: a one; b two.")

    assert "\n- a one\n- b two.\n" in said + "\n"
