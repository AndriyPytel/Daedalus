"""A Step left out as unbuilt is not History: a revision may roll back only the Steps that landed."""
import json

from core.loop.budgets import Budgets
from core.loop.gate import nobody_to_ask
from tests.loop_helpers import code_reply, drive, failed, plan_reply


def test_a_rollback_after_a_skipped_round_undoes_only_the_steps_that_landed():
    result, _, _, tools = drive(
        [plan_reply("Pad the outline 10 mm", "Fillet the four corner edges at R2",
                    "Pad the boss 5 mm"),
         json.dumps({"code": "pad()", "step": 1, "label": "Step"}),
         json.dumps({"code": "fillet()", "step": 2, "label": "Step"}),
         json.dumps({"declare": {"steps": [{"step": 1, "label": "Step"}]}}),
         json.dumps({"declare": {"obsolete": "the outline is not what the boss sits on"}}),
         plan_reply("Pad the outline 12 mm", "Pad the boss 5 mm",
                    rollback={"to": 0, "why": "the outline was the wrong size"}),
         code_reply("pad12()", "boss()")],
        applies=[None, failed("FilletId is not part of the shape")],
        budgets=Budgets(attempts_per_step=1), ask=nobody_to_ask)

    assert result.ok, result.reason
    assert tools.applied_codes()[-2:] == ["pad12()", "boss()"]
