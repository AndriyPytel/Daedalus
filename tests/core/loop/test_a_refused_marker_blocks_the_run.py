"""
On an edit, a Design that still carries a bad `at` (or no `changed` feature) after its re-asks stops the run as blocked before Plan, saying which feature and which entries.
"""
from tests.loop_helpers import _turns_of, design_reply, drive, plan_reply

BASE = "The part before the edit, in mm:\nplanes:\n  plane y = -17.5 (area 1605.35 mm2)"
FACES = [{"base": {"plane y = -17.5": 1605.35}, "now": []}] * 2


def _model(edit="changed", at=("the bore axis", "boss diameter 100, axis (0, 1, 0)")):
    return {"features": [{"id": "boss-centre", "edit": edit, "at": list(at)}]}


def _drive(model, rereads=3):
    return drive([design_reply(model)] * (1 + rereads) + [plan_reply("Pad it")],
                 base=BASE, faces=FACES)


def test_a_refused_at_blocks_the_run_before_plan_and_names_the_feature_and_its_entries():
    result, events, llm, tools = _drive(_model())

    assert result.blocked and not result.ok
    assert "boss-centre" in result.reason
    assert "the bore axis" in result.reason and "boss diameter 100, axis (0, 1, 0)" in result.reason
    assert _turns_of(llm, "plan") == []
    assert ("apply_step", {}) not in [(n, {}) for n, _ in tools.calls]


def test_a_design_with_no_changed_feature_blocks_and_says_so():
    result, _, llm, _ = _drive(_model(edit="kept", at=()))

    assert result.blocked and "no feature" in result.reason and "changed" in result.reason
    assert _turns_of(llm, "plan") == []


def test_an_at_the_base_part_lists_goes_on_to_plan():
    result, _, llm, _ = drive([design_reply(_model(at=["plane y = -17.5"])), plan_reply("Pad it")],
                              base=BASE, faces=FACES)

    assert _turns_of(llm, "plan")
