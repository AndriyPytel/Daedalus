"""Where nobody answers, a Plan found out of date after a Step was left out is handed back with that Step's reason, not as an obsolete revision."""
import json

from core.events import PLAN_REVISED
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import code_reply, drive, nothing_to_add, plan_reply

STEPS = ("Pad the outline 10 mm", "Pad the boss 5 mm", "Pad the lug 3 mm")


def _run(second, ask=nobody_to_ask):
    _, events, _, _ = drive(
        [plan_reply(*STEPS),
         json.dumps({"code": "pad()", "step": 1, "label": "Step"}),
         json.dumps({"declare": {"steps": [{"step": 1, "label": "Step"}], "unbuildable": "the boss has no face"}}),
         second,
         plan_reply("Pad the boss 5 mm from the top", "Pad the lug 3 mm"),
         code_reply("boss()", "lug()", first=2), nothing_to_add()],
        ask=ask)
    return events_of(events, PLAN_REVISED)


def test_obsolete_after_a_deferred_step_is_a_handback_carrying_its_reason():
    revisions = _run(json.dumps({"declare": {"obsolete": "Step 3 needs the boss"}}))

    assert len(revisions) == 1
    assert revisions[0].data["because"] == "Execute could not build a Step"
    assert "the boss has no face" in revisions[0].data["reason"]
    assert "Step 3 needs the boss" in revisions[0].data["reason"]


def test_obsolete_with_nothing_left_out_is_the_ordinary_revision():
    _, events, _, _ = drive(
        [plan_reply(*STEPS), json.dumps({"declare": {"obsolete": "The Document is empty"}}),
         plan_reply("Pad the outline 10 mm"), code_reply("pad()"), nothing_to_add()],
        ask=nobody_to_ask)

    revisions = events_of(events, PLAN_REVISED)
    assert [r.data["because"] for r in revisions] == ["Execute found the Plan out of date"]
