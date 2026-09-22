"""
A drifted attempt (`SignatureMismatch`) goes through the same bookkeeping as
any other refused attempt, instead of returning before `tried[number]`,
`PREFLIGHT_FAILED`, `_the_same_way_twice` and `refused[number]` ever run.
"""
from core.events import PREFLIGHT_FAILED
from core.loop.budgets import Budgets
from core.loop.execute import _classified_failures

from tests.loop_helpers import (
    _execute_attempts, drifted, drive, plan_reply, retried_code_reply, suspect_reply,
    verdict_reply,
)

SIGNATURE_MISMATCH = (
    "The replayed Step's signature did not match what the kernel verified -- "
    "expected {'objects': 2}, landed {'objects': 1}. It was rolled back rather "
    "than silently accepted."
)


def test_a_drifted_attempt_is_appended_to_tried_and_reaches_the_directive():
    """AC1, AC3: a second drifted attempt is compared against the first by
    `_the_same_way_twice`, which reads only what `tried[number]` holds."""
    _, _, llm, _ = drive([
        plan_reply("Pad the outline"),
        retried_code_reply("first()", "second()", "third()"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[drifted(SIGNATURE_MISMATCH), drifted(SIGNATURE_MISMATCH), None])

    retried = [turn for turn in _execute_attempts(llm)
               if "you_have_failed_this_the_same_way_twice" in turn]
    assert retried, "two drifted attempts at the same Step never reached the directive"
    assert "failed 2 times with the same failure" in         retried[0]["you_have_failed_this_the_same_way_twice"]


def test_a_drifted_attempt_emits_preflight_failed():
    """AC2: drift fires `PREFLIGHT_FAILED` the same as any other refusal."""
    _, events, _, _ = drive([
        plan_reply("Pad the outline"),
        retried_code_reply("first()", "second()", step=1),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[drifted(SIGNATURE_MISMATCH), None])

    failures = events.of(PREFLIGHT_FAILED)
    assert failures, "a drifted attempt emitted no PREFLIGHT_FAILED event"
    assert failures[0].data["step"] == 1


def test_a_step_that_drifts_attempts_per_step_times_is_handed_back_to_plan():
    """AC4: drift counts against `refused[number]` and the existing ceiling --
    a Step that drifts every time is handed back to Plan rather than retried
    forever."""
    _, _, llm, _ = drive([
        plan_reply("Pad the outline"),
        retried_code_reply("zero()", "one()", "two()", "three()"),
        plan_reply("Pad the outline, differently"),
        suspect_reply(), verdict_reply("pass"),
    ], applies=[drifted(SIGNATURE_MISMATCH)] * 4, budgets=Budgets(attempts_per_step=3))

    to_plan = _turns_of_plan_text(llm)
    assert "failed the same way" in to_plan
    assert "Rewording it will send Execute back at the same wall" in to_plan


def _turns_of_plan_text(llm):
    import json
    from tests.loop_helpers import _turns_of
    return json.dumps(_turns_of(llm, "plan")[1])


def test_classified_failures_has_a_signature_mismatch_entry():
    """AC5: `_classified_failures()` fires on a drifted attempt's traceback,
    so a drifted Step's directive names the mismatch like any other."""
    matched = [phrase for phrase, _directive, _label in _classified_failures()
               if phrase in SIGNATURE_MISMATCH]
    assert matched, "no _classified_failures() entry recognises a SignatureMismatch traceback"
