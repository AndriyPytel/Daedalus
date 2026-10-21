"""Rules Plan is given only where nobody answers."""
import pytest

from core.loop.prompt import prompt
from core.nobody import nobody_answering

RULES = {
    "depth": "A depth you assume for a pocket into a wall or flange leaves material behind it -- no more than half that wall -- unless the description says it goes through",
    "wall": "A recess printed N mm deep into a wall means the wall is thicker than N; never plan a wall thinner than the deepest recess cut into it plus 1 mm.",
    "blanket": "A fillet or chamfer Step over \"all remaining\", \"all concave\" or \"all other\" edges is not planned",
}


@pytest.mark.parametrize("rule", RULES.values())
def test_plan_has_the_rule_only_where_nobody_answers(rule):
    with nobody_answering():
        headless = " ".join(prompt("plan").split())
    asked = " ".join(prompt("plan").split())

    assert rule in headless and rule not in asked


def test_plan_computes_an_outline_from_a_details_numbers_as_a_closed_list_of_points_in_every_mode():
    said = " ".join(prompt("plan").split())

    assert ("An outline drawn from a detail's numbers -- a pocket, a web field, a plate profile -- is computed "
            "in your kernel as a closed list of points and named in the Step's values") in said
