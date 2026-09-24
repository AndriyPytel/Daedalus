"""
`plan.md` must state FCC-453's four fillet-ordering rules verbatim enough to
grep for: sketch radii, fillets-last with largest-first, the early-fillet
exception, and edge counts as estimates, not facts.
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
    assert "is an estimate for Execute to check" in flat
