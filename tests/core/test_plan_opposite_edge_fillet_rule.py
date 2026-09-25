"""
`plan.md` must state that two fillets on opposite edges of one face are one
decision: Plan either writes both edges into one Step, when that does not mix
concavities, or keeps one and states in the Step why the other is not
planned. A face narrow enough for the first fillet to consume before the
second one runs finds no edge or no face left for the Step that comes after.
"""
import re

from core.prompts import read_prompt


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_plan_md_states_the_opposite_edge_fillet_rule():
    flat = _flat(read_prompt("plan.md"))
    assert ("Two fillets on opposite edges of one face are one decision") in flat
    assert ("Plan either writes both edges into one Step, when that does not "
            "mix concavities, or keeps one and states in the Step why the "
            "other is not planned") in flat
