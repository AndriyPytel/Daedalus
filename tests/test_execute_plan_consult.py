"""
FCC-188 — the Execute→Plan advisory-consult contract.

One direction only: Execute asks Plan when a Step's Preflight fails, never the
other way, because Plan cannot ask about a failure that has not happened yet.
Not a bypass of the Plan Gate (ADR-0013), because the Gate never saw code —
only the Step prose, which is what `promise` carries to Plan here, unchanged.
A different approach to the same promise stays inside the self-correction
space ADR-0008 already grants a retry; advice that changes the promise is
caught the same way an unadvised bad rewrite already is, by Preflight failing
against the promise on the next attempt and the existing hand-back edge
firing — no second gate is built for it here.

`CONSULT` and `CONSULT_RESPONSE` are new kinds rather than a repurposing of
`TOOL_CALL`/`THOUGHT`: neither existing kind carries `step_number`, `promise`
and `failure_reason` as a matter of shape (`core/events.py`), and a shape a
kind does not carry is a shape nobody can rely on it having.

`consult=True` is what turns the mechanism on, off by default the same way
`agentic` is (FCC-183, FCC-187): every test below drives `loop.run` through
`tests.test_loop.drive` with `consult=True`, so nothing here depends on
FreeCAD or a real Preflight — `applies=[failed(...), ...]` stands in for it,
same as every other Preflight test in `tests/test_loop.py`.
"""
import json

from core.events import (
    APPLIED, BLOCKED, CONSULT, CONSULT_RESPONSE, PLAN_REVISED, PREFLIGHT_FAILED,
    TOOL_CALL,
)
from core.loop import Budgets
from tests.test_loop import code_reply, drive, failed, plan_reply, verdict_reply


def advice_reply(text):
    return json.dumps({"advice": text})


def test_a_consult_after_a_preflight_failure_spends_an_attempt():
    """
    The number an Acceptance Criterion names, produced by a test: the same
    `attempts` list `_work_through` already builds is what the budget is
    spent against, one entry per failed Preflight — a consult adds no entry
    of its own.
    """
    budgets = Budgets(attempts_per_step=3)
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            advice_reply("Set Reversed instead of changing the profile."),
            code_reply("try2()"),
            verdict_reply("pass"),
        ],
        applies=[failed("Pad ran the wrong way"), None],
        budgets=budgets,
        consult=True,
    )

    assert result.ok, result.reason
    assert tools.applied_codes() == ["try1()", "try2()"]
    remaining = budgets.attempts_per_step - len(events.of(PREFLIGHT_FAILED))
    assert remaining == 2


def test_the_consult_and_its_response_appear_on_the_stream_in_order_with_their_data():
    result, events, _, _ = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            advice_reply("Set Reversed instead of changing the profile."),
            code_reply("try2()"),
            verdict_reply("pass"),
        ],
        applies=[failed("Pad ran the wrong way"), None],
        consult=True,
    )

    kinds = [event.kind for event in events]
    preflight_failed = kinds.index(PREFLIGHT_FAILED)
    consult = kinds.index(CONSULT)
    consult_response = kinds.index(CONSULT_RESPONSE)
    retry = next(index for index, event in enumerate(events)
                if event.kind == TOOL_CALL and event.data.get("name") == "apply_step"
                and event.data.get("arguments", {}).get("code") == "try2()")

    assert preflight_failed < consult < consult_response < retry

    consulted = events[consult]
    assert consulted.data == {
        "step_number": 1, "promise": "Only", "failure_reason": "Pad ran the wrong way",
    }
    answered = events[consult_response]
    assert answered.data == {
        "step_number": 1, "advice": "Set Reversed instead of changing the profile.",
    }


def test_plan_cannot_initiate_a_consult():
    """
    The one direction FCC-168 draws, read straight off the request: Plan's own
    turn never carries a consult key, because nothing here ever hands Plan a
    tool that lets it start one, and a plain Plan turn scripted with no
    Preflight failure at all never reaches `consult_plan`.
    """
    result, events, llm, tools = drive([
        plan_reply("Only"),
        code_reply("build()"),
        verdict_reply("pass"),
    ], consult=True)

    assert result.ok, result.reason
    assert events.first(CONSULT) is None
    assert tools.applied_codes() == ["build()"]
    plan_request = json.loads(llm.calls[1][-1]["content"])
    assert "consult" not in json.dumps(plan_request).lower()


def test_advice_that_repeats_a_failed_approach_is_still_caught_the_same_way_twice():
    """
    `_the_same_way_twice` reads the plain `attempts` list, which a consult
    never adds to — so advice that talks Execute into the identical rewrite a
    second time is caught exactly as if no consult had happened at all.
    """
    same_traceback = "neither removed nor added any material"
    result, events, llm, tools = drive(
        [
            plan_reply("Only"),
            code_reply("cut_one()"),
            advice_reply("Try the same direction again."),
            code_reply("cut_two()"),
            advice_reply("Try the same direction a third time."),
            code_reply("cut_three()"),
            advice_reply("Try the same direction a fourth time."),
        ],
        applies=[failed(same_traceback), failed(same_traceback), failed(same_traceback)],
        budgets=Budgets(attempts_per_step=3, revisions=0),
        consult=True,
    )

    assert not result.ok
    third_attempt_request = json.loads(llm.calls[6][-1]["content"])
    assert "you_have_failed_this_the_same_way_twice" in third_attempt_request
    assert tools.applied_codes() == ["cut_one()", "cut_two()", "cut_three()"]


def test_advice_that_changes_the_promise_still_fails_preflight_and_hands_back():
    """
    Plan advising a different part — "25mm cube" against a Promise of "20mm
    cube" — is not a mechanism this catches on its own: Preflight is scripted
    to keep failing against the Step's original, unchanged Promise, and once
    attempts are exhausted the existing `PLAN_REVISED` edge fires with a new
    Step, precisely because nothing here is a second gate over the advice.
    """
    result, events, _, tools = drive(
        [
            plan_reply("Pad a 20mm cube"),
            code_reply("cube(20)"),
            advice_reply("Build a 25mm cube instead."),
            code_reply("cube(25)"),
            advice_reply("Build a 25mm cube instead, once more."),
            code_reply("cube(25)"),
            advice_reply("Build a 25mm cube instead, a third time."),
            plan_reply("Pad a 25mm cube"),
            code_reply("cube(25)"),
            verdict_reply("pass"),
        ],
        applies=[failed("does not match the 20mm Promise"),
                failed("does not match the 20mm Promise"),
                failed("does not match the 20mm Promise"), None],
        budgets=Budgets(attempts_per_step=3, revisions=1),
        consult=True,
    )

    assert result.ok, result.reason
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    assert revised.data["steps"] == ["Pad a 25mm cube"]
    assert tools.applied_codes() == ["cube(20)", "cube(25)", "cube(25)", "cube(25)"]


def test_the_gate_is_not_re_opened_by_a_consult():
    """
    ADR-0013 unchanged: the Gate approves Steps once, before any of them run,
    and a consult that happens after the Gate — inside the retry loop of a
    Step already approved — never asks it again.
    """
    from core.events import GATE

    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            advice_reply("Set Reversed instead of changing the profile."),
            code_reply("try2()"),
            verdict_reply("pass"),
        ],
        applies=[failed("Pad ran the wrong way"), None],
        consult=True,
    )

    assert result.ok, result.reason
    asked = [event for event in events.of(GATE) if event.data.get("asked")]
    assert len(asked) == 1
    assert tools.applied_codes() == ["try1()", "try2()"]


def test_exactly_three_consult_events_fire_before_the_handback():
    """
    Three failing attempts at `attempts_per_step=3`: every one of them is a
    consult, the exhausting third included, so the `PLAN_REVISED` that follows
    still carries Plan's word on the Step it is being handed back. A fourth
    consult would be a channel this ticket explicitly refuses to open.
    """
    same_traceback = "neither removed nor added any material"
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            advice_reply("advice one"),
            code_reply("try2()"),
            advice_reply("advice two"),
            code_reply("try3()"),
            advice_reply("advice three"),
            plan_reply("Only, differently"),
            code_reply("try4()"),
            verdict_reply("pass"),
        ],
        applies=[failed(same_traceback), failed("a different failure"),
                failed("yet another failure"), None],
        budgets=Budgets(attempts_per_step=3, revisions=1),
        consult=True,
    )

    assert result.ok, result.reason
    consults = events.of(CONSULT)
    assert len(consults) == 3
    kinds = [event.kind for event in events]
    assert kinds.index(PLAN_REVISED) > kinds.index(CONSULT)
    assert tools.applied_codes() == ["try1()", "try2()", "try3()", "try4()"]
