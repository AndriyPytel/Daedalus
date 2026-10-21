"""What Plan is told when Execute could not build material Steps."""
from core.loop.run import _could_not_be_built


def test_the_handback_keeps_the_extent_of_the_step_and_has_plan_choose_a_missing_number():
    said = " ".join(_could_not_be_built(["Pocket the cavity 270 x 270 x 100"],
                                        [{"index": 0, "why": "no arm width"}]).split())

    assert "Execute: no arm width" in said and "send it back among the Steps still to build" in said
    assert "choose that number, bind it as a value in your kernel and name it in the Step's values" in said
    assert "never a smaller feature in its place" in said
    assert "compute that outline yourself in your kernel as a closed list of points" in said
    assert "Execute draws what you computed and never arranges numbers itself" in said
