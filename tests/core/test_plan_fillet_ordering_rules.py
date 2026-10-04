"""
`plan.md` must state the fillet-ordering rules verbatim enough to grep for:
sketch radii, fillets-last with largest-first, the early-fillet exception,
and edge counts written as approximate, not as fact.
"""
import re

from core.prompts import read_prompt


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_plan_md_states_the_fillet_ordering_rules():
    flat = _flat(read_prompt("plan.md"))
    assert "belongs to a profile is drawn in the sketch" in flat
    assert "largest radius first" in flat
    assert "cut that edge into pieces" in flat
    assert "written as approximate, not as a fact" in flat


def test_plan_md_states_the_fillets_last_rule_names_its_two_exceptions():
    flat = _flat(read_prompt("plan.md"))
    assert ("a fillet or a chamfer only once every one of those stands — "
            "except the two cases named in the paragraph on early rounds below") in flat
    assert ("A round planned in the middle of the shape is a round the "
            "Steps after it move out from under — outside the "
            "two cases named in the paragraph on early rounds") in flat
