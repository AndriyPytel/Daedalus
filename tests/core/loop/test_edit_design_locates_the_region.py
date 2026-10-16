"""On an edit, Design is told to list the candidate faces and bores, pick one and check it against the intent's direction; a fresh design is not."""
from core.loop import design as design_module
from core.prompts import read_prompt


def _said(body):
    return " ".join(str(body).split())


def test_an_edit_body_tells_design_to_list_candidates_pick_one_and_check_the_direction():
    said = _said(design_module._body("raise the small bore", "plane z = 10"))

    assert "candidate faces and bores" in said
    assert "address, axis, diameter or normal, and position" in said
    assert "Pick one" in said
    assert "against the sign and direction the intent gives" in said
    assert "in `because`" in said


def test_a_run_without_a_base_part_is_not_told():
    said = _said(design_module._body("a 10mm box", None))

    assert "candidate" not in said


def test_the_locate_rule_does_not_contradict_what_mark_what_changes_asks():
    locate = read_prompt("locate_the_edit.md")

    assert "base_faces" in locate and "at" in read_prompt("mark_what_changes.md")
