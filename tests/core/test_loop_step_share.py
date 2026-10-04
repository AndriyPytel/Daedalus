"""
A Step's turns are its own share of the run's pool, and a headless Plan asks
at most `Budgets.unanswered_questions` questions nobody answers.

Recorded runs: one spent the whole pool on retries (64 applies over 19 Steps)
and stopped at Step 10; another spent it on about 100 questions before Step 1.
"""
import json

from core.agent import subagent as sa
from core.events import ANSWER, PLAN_REVISED, TURN_BUDGET_EXHAUSTED
from core.loop.budgets import Budgets
from tests.loop_helpers import (_MultiTurn, code_reply, design_reply, drive, failed,
                                plan_reply, question_reply, suspect_reply, verdict_reply)


def test_a_step_that_spends_its_share_is_exhausted_while_the_run_still_has_turns():
    supervisor = sa.SessionTreeSupervisor(turn_budget=3 * 4 + sa.RESERVE)
    supervisor.begin_step(remaining=4)
    for _ in range(supervisor.step_budget):
        supervisor.record_turn()
    assert supervisor.outcome is None
    supervisor.record_turn()
    assert supervisor.outcome == sa.BUDGET_EXHAUSTED
    assert supervisor.run_outcome is None
    assert supervisor.exhaustion_reason()["scope"] == "step"


def test_a_bad_step_cannot_take_the_turns_the_later_steps_need():
    supervisor = sa.SessionTreeSupervisor(turn_budget=3 * 10 + sa.RESERVE)
    supervisor.begin_step(remaining=10)
    assert supervisor.step_budget <= 30 - 3 * 9


def test_turns_a_landed_step_did_not_spend_go_back_to_the_next_ones():
    supervisor = sa.SessionTreeSupervisor(turn_budget=3 * 2 + sa.RESERVE)
    supervisor.begin_step(remaining=2)
    first = supervisor.step_budget
    supervisor.record_turn()
    supervisor.begin_step(remaining=1)
    assert supervisor.step_budget > first


def test_the_next_step_starts_with_none_of_the_last_steps_turns_spent():
    supervisor = sa.SessionTreeSupervisor(turn_budget=40)
    supervisor.begin_step(remaining=5)
    supervisor.record_turn()
    supervisor.record_turn()
    supervisor.begin_step(remaining=4)
    assert supervisor.step_turns == 0


def test_a_run_wide_wall_is_still_the_runs_own_outcome():
    supervisor = sa.SessionTreeSupervisor(turn_budget=2)
    supervisor.begin_step(remaining=1)
    for _ in range(3):
        supervisor.record_turn()
    assert supervisor.run_outcome == sa.BUDGET_EXHAUSTED
    assert supervisor.exhaustion_reason()["scope"] == "run"


STEPS = ["Step %d" % n for n in range(1, 11)]
CODES = ["op%d()" % n for n in range(1, 11)]


def test_one_step_retried_past_its_share_is_handed_back_not_the_end_of_the_run():
    result, events, _, tools = drive(
        [
            plan_reply(*STEPS),
            _MultiTurn([json.dumps({"code": "bad%d()" % n, "step": 1, "label": "Step"})
                        for n in range(1, 5)]
                       + [json.dumps({"declare": {"unbuildable": "still failing"}})]),
            plan_reply(*STEPS),
            code_reply(*CODES),
            suspect_reply(), verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        budgets=Budgets(attempts_per_step=10),
    )

    assert result.ok, result.reason
    assert tools.applied_codes()[-len(CODES):] == CODES
    assert [event.data["scope"] for event in events.of(TURN_BUDGET_EXHAUSTED)] == ["step"]
    assert events.first(PLAN_REVISED) is not None


def _asks(count):
    return [question_reply("What is q%d?" % n) for n in range(count)]


def test_the_question_after_the_third_unanswered_one_is_told_to_decide_and_write_assumed():
    result, events, llm, _ = drive(
        _asks(4) + [plan_reply("Sketch it"), code_reply("a()"), suspect_reply(),
                    verdict_reply("pass")],
        budgets=Budgets(settles=0),
    )

    assert result.ok, result.reason
    answers = [event.data["text"] for event in events.of(ANSWER) if event.data["by"] == "nobody"]
    assert len(answers) == 4
    assert "assumed" not in answers[2]
    assert "assumed" in answers[3]


def test_a_plan_that_keeps_asking_after_it_was_told_to_decide_stops_the_run():
    result, events, llm, _ = drive(_asks(12), budgets=Budgets(settles=0))

    assert not result.ok
    assert len([event for event in events.of(ANSWER) if event.data["by"] == "nobody"]) == 4
    assert len(llm.calls) <= 6
