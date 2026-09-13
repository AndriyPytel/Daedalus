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
    it.
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


def test_a_chain_is_one_claim_and_the_last_number_is_the_answer():
    """
    A Step that shows its working writes `a = b = c`, and the middle of a chain
    is a stretch of the sum rather than an answer to it. Read as an answer, the
    hexagon Step of the recorded run
    (`logs/sessions/069d21dbac10416392533fe7c06b62d4.jsonl`) was handed back
    twice — `11 / (2 * cos(30°)) = 5.5, and it is 6.35` — over a circumradius it
    had right in the first Plan it wrote, and the two revisions it spent were
    the two the run needed when the cutout came back unbuildable.
    """
    assert wrong_sums("the circumradius is 11 / (2 * cos(30°)) = 5.5 / 0.86603 "
                      "= 6.351 mm") == []
    assert wrong_sums("the apothem is 11 / 2 = 5.5 mm and the circumradius is "
                      "5.5 / cos(30°) = 5.5 / 0.866025 = 6.351 mm") == []

    wrong = wrong_sums("the circumradius is 11 / (2 * cos(30°)) = 5.5 / 0.86603 "
                       "= 5.9 mm")

    assert len(wrong) == 1
    assert "11 / (2 * cos(30°)) = 5.9" in wrong[0]
    assert "6.35" in wrong[0]


def test_an_inverse_is_not_the_function_its_name_ends_with():
    """
    The recorded run
    (`logs/sessions/45ee20f1f9a34c60a79bfd62d2dace8c.jsonl`) put the rib's
    slope at `atan(15/45) = 18.43°`, which comes out. Read as the `tan` its
    name ends with it is 0.01, and a revision went on rewriting a Plan whose
    number was right all along.
    """
    assert wrong_sums("the upper face rises at atan(15/45) = 18.43° from the toe") == []
    assert wrong_sums("the slope angle atan(15/45) = 18.435°") == []


def test_an_inverse_that_does_not_come_out_is_still_wrong():
    wrong = wrong_sums("the upper face rises at atan(15/45) = 12°")

    assert len(wrong) == 1
    assert "18.43" in wrong[0]


def test_an_inverse_answers_in_degrees():
    """A drawing is in degrees, so what asks an angle of a ratio answers in them."""
    assert wrong_sums("asin(0.5) = 30°") == []
    assert wrong_sums("acos(0.5) = 60°") == []


def test_the_function_still_means_itself():
    """The name the inverse ends with keeps its own meaning and its own answer."""
    wrong = wrong_sums("the slope is tan(15/45) = 18.43°")

    assert len(wrong) == 1
    assert "0.01" in wrong[0]


def test_a_fraction_written_as_the_answer_is_not_a_claim():
    """
    The recorded run (`logs/sessions/5d9249424bce45d2b25485002ba1c630.jsonl`)
    wrote a slope as `15/45 = 1/3`, and the bare `1` of `1/3` was read as the
    answer, reporting `15/45 = 1, and it is 0.33` over a slope that was right.
    An answer immediately followed by `/` and a digit is a fraction, not a
    number, and is not a claim.
    """
    assert wrong_sums("of slope 15/45 = 1/3 (the finished top edge rises 15 mm "
                      "over the 45 mm ...)") == []


def test_a_square_root_sign_is_read():
    """
    The same run wrote a spine length as `√(55² + 18.33²) = √(3025 + 336) =
    57.97 mm`. With `√` unread the match started after it, dropping the root
    and reporting `(3025 + 336) = 57.97, and it is 3361`.
    """
    assert wrong_sums("a spine of length √(55² + 18.33²) = √(3025 + 336) = "
                      "57.97 mm") == []


def test_a_square_root_that_does_not_come_out_is_still_wrong():
    """Fixing the false positive above must not make `√` a blanket exemption."""
    wrong = wrong_sums("the spine is √(16) = 5 mm")

    assert len(wrong) == 1
    assert "4" in wrong[0]


def test_a_chain_that_ends_in_a_sum_is_not_a_claim():
    """
    The same run wrote `18 + 2 = 20 mm = 5 + 15 mm`, a chain whose last
    stretch is a sum rather than a number. The regex stopped on the bare `5`
    of `5 + 15` and reported `18 + 2 = 5, and it is 20` over an addition
    nobody got wrong.
    """
    assert wrong_sums("the finished crest top is 18 + 2 = 20 mm = 5 + 15 mm "
                      "above the top face") == []
