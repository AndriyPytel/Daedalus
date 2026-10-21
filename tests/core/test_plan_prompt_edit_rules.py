"""Rules Plan is given only on an edit."""
from core.loop.prompt import prompt

RULE = "is removed by the revolved or extruded region it occupies"


def test_plan_is_told_how_to_remove_a_feature_a_sketch_cannot_cut_only_on_an_edit():
    editing = " ".join(prompt("plan", editing=True).split())
    building = " ".join(prompt("plan").split())

    assert RULE in editing and "a PolarPattern cannot copy what exists only inside BaseFeature" in editing
    assert RULE not in building
