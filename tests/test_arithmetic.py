"""
Level 1 — the arithmetic a Step states about itself.

The sums are the ones from the run that named the defect
(`logs/sessions/4fca495a8076439a9967a584abd05227.jsonl`, Model Mania 2023): a
45° arm placed by a station nobody measured, and a Plan that wrote the station
down twice without ever multiplying it by the cosine.
"""
from core.arithmetic import wrong_sums


def test_a_sum_that_comes_out_is_left_alone():
    assert wrong_sums("assumed arm station 65.5 / cos(45°) = 92.63 mm") == []


def test_the_sum_the_recorded_run_never_did():
    """
    The Step claimed an 80.5 mm station put the arm's extreme on the 0 mm
    tangent plane. It puts it at 8.58, which is what Execute worked out for
    itself, twice, after the Plan had reached the Gate and the user had approved
    it. FCC-127.
    """
    wrong = wrong_sums("assume an 80.5 mm station, so the left extreme is at "
                       "65.5 - 80.5 * cos(45°) = 0 mm")

    assert len(wrong) == 1
    assert "65.5 - 80.5 * cos(45°) = 0" in wrong[0]
    assert "8.58" in wrong[0]


def test_a_rounded_answer_is_the_answer():
    """
    A Step is prose and a draughtsman rounds. What is checked is whether the
    number stated is what the sum comes to at the precision it was stated to.
    """
    assert wrong_sums("25 / 3 = 8.3 mm") == []
    assert wrong_sums("25 / 3 = 8.33 mm") == []
    assert wrong_sums("25 / 3 = 8 mm") == []
    assert wrong_sums("25 / 3 = 8.4 mm") != []


def test_degrees_because_a_drawing_is_in_degrees():
    """`cos(45°)` read as radians is 0.5253, and no drawing means that."""
    assert wrong_sums("100 * cos(60) = 50 mm") == []
    assert wrong_sums("sqrt(2) = 1.414") == []


def test_prose_that_states_no_sum_states_nothing_to_check():
    for said in ("Pad the profile 30 mm front to rear",
                 "Cut the Ø6.6 × 8.0 obround slot through all",
                 "Set Type = ThroughAll",
                 "Set the Length = 30",
                 "Place the ring axis 65.5 mm from the left end tangent plane"):
        assert wrong_sums(said) == [], "a Step with no arithmetic in it was checked: " + said


def test_a_number_restated_is_not_a_sum():
    """
    `= 30` after a number is a label, not a claim: there is nothing to work out
    and nothing that can be wrong. Only a sum somebody had to do is checked.
    """
    assert wrong_sums("the depth 30 = 30 mm") == []


def test_arithmetic_that_cannot_be_worked_out_is_not_arithmetic_that_is_wrong():
    """
    The check never fails a Step for prose it does not understand: what it
    cannot evaluate it says nothing about, because the alternative is a Plan
    handed back over a sentence nobody can fix.
    """
    for said in ("the width w / 2 = 7 mm", "10 / 0 = 0 mm",
                 "the R2 fillet = 2 mm", "2 ** 4096 = 0"):
        assert wrong_sums(said) == [], "the check refused prose it could not read: " + said


def test_every_sum_in_the_step_is_checked():
    wrong = wrong_sums("the extreme is 65.5 - 80.5 * cos(45°) = 0 mm and the lug ends at "
                       "65.5 + 42.5 = 109 mm")

    assert len(wrong) == 2
