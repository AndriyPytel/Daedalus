"""
An Assembly edit approves the deletion of the part its Intent names and the joints attached to it, and nothing else.
"""
from types import SimpleNamespace

from core.arch import named_in


def _document(objects):
    return SimpleNamespace(getObject=objects.get)


def _joint(label, *parts):
    joint = SimpleNamespace(Label=label, JointType="Fixed", Reference1=[parts[0], ["", ""]],
                            Reference2=[parts[1], ["", ""]], OutList=list(parts), InList=[])
    for part in parts:
        part.InList.append(joint)
    return joint


def _chain():
    part1, part2, part3 = (SimpleNamespace(Label="Part%d" % n, OutList=[], InList=[]) for n in (1, 2, 3))
    ground = SimpleNamespace(Label="Ground", ObjectToGround=part1, OutList=[part1], InList=[])
    part1.InList.append(ground)
    joints = {"J12": _joint("Joint", part1, part2), "J23": _joint("Joint001", part2, part3)}
    parts = {"Part1": part1, "Part2": part2, "Part3": part3, "Ground": ground}
    return _document(parts | joints), parts, joints


def test_the_part_the_intent_names_and_its_joints_are_approved():
    document, _, _ = _chain()

    assert named_in("Remove Part3 and its joint from the assembly.", document, ["Part3", "J23"])
    assert named_in("Remove Part2 and its joints.", document, ["Part2", "J12", "J23"])


def test_a_part_the_intent_does_not_name_is_not_approved():
    document, _, _ = _chain()

    assert not named_in("Remove Part3 and its joint from the assembly.", document, ["Part3", "J23", "Part2"])
    assert not named_in("Remove Part3 and its joint from the assembly.", document, ["Part1"])


def test_a_joint_of_a_part_the_intent_does_not_name_is_not_approved():
    document, _, _ = _chain()

    assert not named_in("Remove Part3 and its joint from the assembly.", document, ["Part3", "J12"])


def test_the_ground_of_a_part_that_is_named_goes_with_it():
    document, _, _ = _chain()

    assert named_in("Remove Part1.", document, ["Part1", "Ground", "J12"])


def test_an_intent_that_names_no_part_approves_nothing():
    document, _, _ = _chain()

    assert not named_in("Remove the top bracket and its joint.", document, ["Part3", "J23"])


def test_a_part_label_inside_a_longer_label_is_not_named():
    document, _, _ = _chain()

    assert not named_in("Remove Part30.", document, ["Part3"])
