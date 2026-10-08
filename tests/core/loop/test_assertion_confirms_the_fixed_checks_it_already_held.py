"""
A recorded Assertion phase's fixed envelope check held while the model gave
up on the checks it was asked to write, replying `{"declare": {}}` after
budgets.attempts_per_step was spent. Nothing the fixed checks already held
used to survive into `confirmed`, so a run whose only measurement was the
fixed checks reported having confirmed nothing at all -- and Review, told
`facts_the_checks_confirmed` was empty, could still return a plain `pass`.
"""
import json

from core.events import ASSERTION, EventStream
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.loop.budgets import Budgets
from tests.loop_helpers import (
    FakeTools, code_reply, design_reply, drive, everything, plan_reply, suspect_reply,
    verdict_reply,
)

_A_POSITION = "features \u2192 hole-a \u2192 position \u2192 the left end face: 20 mm"

_THE_RECORDED_ACCEPTANCE = [
    "envelope → length: 80 mm between the left end face and the right end face (read)",
    "envelope → depth front to rear: 30 mm between the front face and the rear face (read)",
    "envelope → height: 40 mm between the bottom of the lug and the top of the boss (read)",
    _A_POSITION,
]

_A_PART_WITH_AN_ENVELOPE = {
    "envelope": {"length between the ends": {"value": "108 mm", "flag": "read"},
                "depth front to rear": {"value": "30 mm", "flag": "read"},
                "height of the right lug": {"value": "30 mm", "flag": "read"}},
    "features": [{"id": "hole-a", "kind": "hole",
                  "position": {"the left end face": {"value": "20 mm", "flag": "read", "mm": 20}}}],
}


def _the_model_gives_up_after_probing():
    """
    Every `run_phase` turn the recorded session made after the fixed checks:
    six probes (`{"code": ...}`) it never wrapped in a `declare`, then the
    434-token reply that closed the phase, `{"declare": {}}`.
    """
    return [json.dumps({"code": "print('probe %d')" % n}) for n in range(6)] + \
        [json.dumps({"declare": {}})]


def test_an_envelope_the_fixed_check_already_held_is_confirmed_even_when_the_model_gives_up():
    tools = FakeTools().registry
    llm = ScriptedLLMClient(_the_model_gives_up_after_probing())
    events = EventStream()

    checked = assertion(_THE_RECORDED_ACCEPTANCE, {}, tools, llm, events,
                        Budgets(attempts_per_step=1))

    assert "the envelope" in checked.confirmed, \
        "the fixed envelope check already held and must not be discarded"
    assert checked.uncovered == [_A_POSITION]
    assert set(_THE_RECORDED_ACCEPTANCE[:3]) <= set(checked.confirmed)
    reported = events.first(ASSERTION).data
    assert "the envelope" in reported["confirmed"]


def test_a_run_whose_assertion_confirms_nothing_is_not_an_unqualified_pass(monkeypatch):
    """
    `assertion()` itself can no longer return an empty `confirmed` on a
    non-failed path -- the fixed checks always name something -- but the
    contract this guards belongs to `run()`, not to how `assertion()` happens
    to behave today, so it is exercised directly against a `Checked` that
    confirmed nothing.
    """
    import importlib
    loop_run = importlib.import_module("core.loop.run")
    from core.loop.assertion import Checked

    monkeypatch.setattr(loop_run, "assertion",
                        lambda *args, **kwargs: Checked(uncovered=["envelope → length"]))

    result, events, llm, tools = drive([
        design_reply(_A_PART_WITH_AN_ENVELOPE),
        plan_reply("Pad the outline", satisfies=everything(_A_PART_WITH_AN_ENVELOPE)),
        code_reply("pad()"),
        suspect_reply(),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert result.unmet, "a pass over an Assertion that confirmed nothing must carry unmet"



def test_the_recorded_shape_end_to_end_fixed_checks_pass_the_model_gives_up_still_qualified():
    """
    The exact shape of the recorded run, through the real `assertion()`, with
    no monkeypatch: the fixed checks genuinely hold (`confirmed` is not
    empty), the model still gives up without declaring a check of its own,
    and the acceptance lines the fixed checks never touch stay `uncovered`.
    A pass over that must still come back qualified.
    """
    result, events, llm, tools = drive([
        design_reply(_A_PART_WITH_AN_ENVELOPE),
        plan_reply("Pad the outline", satisfies=everything(_A_PART_WITH_AN_ENVELOPE)),
        code_reply("pad()"),
        suspect_reply(),
    ] + _the_model_gives_up_after_probing() + [verdict_reply("pass")],
        budgets=Budgets(attempts_per_step=1))

    give_up_events = [event for event in events.of(ASSERTION) if event.data.get("ok") is None]
    assert give_up_events, "the give-up fallback must have run"
    assert give_up_events[-1].data["confirmed"], "the fixed checks held and must be reported"
    assert result.ok
    assert result.unmet, ("the fixed checks holding is not the whole acceptance list -- a pass "
                          "that never checked it must still carry unmet")
