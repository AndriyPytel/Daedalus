"""
Level 1 -- the Assembly Assertion names what fails, with numbers, and says nothing where nothing fails.
"""
from core.agent.assembly import refusals

SOLVED = {"assemblies": 1, "parts": ["A", "B"], "loose": [], "grounded": ["A"], "joints": 1,
          "solve": 0, "unsatisfied": [], "links": [["A", "B"]], "pairs": [{"a": "A", "b": "B", "common": 0.0, "smaller": 1000.0}]}


def _facts(**fields):
    return SOLVED | fields


def test_a_solved_assembly_with_joints_and_no_interference_is_refused_nothing():
    assert refusals(SOLVED) == []


def test_a_document_with_no_assembly_or_two_is_refused():
    assert "no Assembly::AssemblyObject" in refusals(_facts(assemblies=0))[0]
    assert "2 Assembly::AssemblyObject" in refusals(_facts(assemblies=2))[0]


def test_an_assembly_with_no_joint_is_refused():
    found = refusals(_facts(joints=0))

    assert len(found) == 1 and "no joint" in found[0]


def test_an_assembly_with_no_grounded_part_is_refused_whatever_solve_said():
    found = refusals(_facts(grounded=[]))

    assert len(found) == 1 and "no grounded part" in found[0]


def test_solve_codes_are_named():
    assert "no grounded part" in refusals(_facts(solve=-6))[0] and "-6" in refusals(_facts(solve=-6))[0]
    assert "over-constrained" in refusals(_facts(solve=-4))[0]
    assert "-9" in refusals(_facts(solve=-9))[0]


def test_a_joint_whose_frames_do_not_meet_after_solving_is_over_constrained_and_named():
    stuck = {"joint": "Joint001", "type": "Fixed", "a": "A", "b": "B", "gap": 10.0, "turned": False}

    found = refusals(_facts(unsatisfied=[stuck]))

    assert len(found) == 1 and "over-constrained" in found[0]
    assert "Joint001" in found[0] and "A" in found[0] and "B" in found[0] and "10" in found[0]


def test_a_fixed_joint_whose_frames_are_turned_apart_says_so():
    turned = {"joint": "Joint", "type": "Fixed", "a": "A", "b": "B", "gap": 0.0, "turned": True}

    assert "turned" in refusals(_facts(unsatisfied=[turned]))[0]


def test_parts_outside_the_assembly_are_refused_by_name():
    found = refusals(_facts(loose=["Bolt"]))

    assert len(found) == 1 and "Bolt" in found[0] and "not in the Assembly" in found[0]


def test_interference_over_a_thousandth_of_the_smaller_part_is_refused_with_its_numbers():
    over = {"a": "A", "b": "B", "common": 1.5, "smaller": 1000.0}

    found = refusals(_facts(pairs=[over]))

    assert len(found) == 1 and "A" in found[0] and "B" in found[0] and "1.5" in found[0]


def test_interference_of_exactly_a_thousandth_is_not_refused():
    assert refusals(_facts(pairs=[{"a": "A", "b": "B", "common": 1.0, "smaller": 1000.0}])) == []


def test_each_failing_condition_is_listed():
    found = refusals(_facts(joints=0, solve=-4,
                            pairs=[{"a": "A", "b": "B", "common": 500.0, "smaller": 1000.0}]))

    assert len(found) == 3


def test_a_part_with_no_joint_path_to_the_grounded_part_is_refused_by_name():
    found = refusals(_facts(parts=["A", "B", "C"], links=[["A", "B"]]))

    assert len(found) == 1 and "C" in found[0] and "not joined" in found[0]


def test_a_part_joined_only_through_another_part_is_connected():
    assert refusals(_facts(parts=["A", "B", "C"], links=[["B", "C"], ["A", "B"]])) == []


def test_a_group_of_parts_joined_to_each_other_but_not_to_the_grounded_one_is_refused():
    found = refusals(_facts(parts=["A", "B", "C"], links=[["B", "C"]]))

    assert len(found) == 1 and "B" in found[0] and "C" in found[0]
