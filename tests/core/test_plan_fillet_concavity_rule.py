"""
`plan.md` must state the fillet-concavity rule verbatim enough to grep for:
one fillet Step takes edges of one concavity, and a round spanning both
concavities is split into two Steps, concave first.
"""
import re

from core.prompts import read_prompt


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_plan_md_states_the_fillet_concavity_rule():
    flat = _flat(read_prompt("plan.md"))
    assert "one fillet Step takes edges of one concavity" in flat
    assert ("a round that runs from an inside corner onto an outside edge "
            "is two Steps, concave first") in flat
