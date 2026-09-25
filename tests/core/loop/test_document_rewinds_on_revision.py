"""Loop level -- a Plan revision must rewind the Document to the Steps it keeps."""
from core.events import TOOL_CALL

from tests.loop_helpers import code_reply, drive, obsolete_reply, plan_reply, suspect_reply, verdict_reply


def _undo_calls(tools):
    return [call for call in tools.calls if call[0] == "undo_step"]


def test_a_revision_that_drops_an_applied_step_undoes_it_from_the_document():
    """
    Steps 1 and 2 land, Step 3 goes obsolete, and the revision rewords Step
    2 as well as Step 3 -- so the revised Plan only carries Step 1. The
    Document must lose what the old Step 2 created before Execute is asked
    to declare the new one, or it holds an object no Step in the revised
    Plan ever declared.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the base", "Boss the arm", "Drill the hole"),
        code_reply("pad()", first=1),
        code_reply("boss()", first=2),
        obsolete_reply("The arm width changed"),
        plan_reply("Pad the base", "Boss the wider arm", "Drill the smaller hole"),
        code_reply("boss_wider()", first=2),
        code_reply("drill_smaller()", first=3),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[
        {"status": "applied", "created": ["PadBase"], "description": {}},
        {"status": "applied", "created": ["BossArm"], "description": {}},
        {"status": "applied", "created": ["BossWiderArm"], "description": {}},
        {"status": "applied", "created": ["DrillHole"], "description": {}},
    ])

    assert result.ok, result.reason
    assert len(_undo_calls(tools)) == 1, (
        "Step 2 was applied then dropped by the revision, so exactly one Step "
        "must be undone -- got %r" % _undo_calls(tools))
    assert "BossArm" not in tools.created, (
        "the Document still holds what the dropped Step 2 created: %r" % tools.created)
    assert tools.created == ["PadBase", "BossWiderArm", "DrillHole"]


def test_a_revision_that_keeps_every_landed_step_undoes_nothing():
    """
    The revision only rewords Step 3, which never applied -- Steps 1 and 2
    are carried whole, so the Document must not be touched.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the base", "Boss the arm", "Drill the hole"),
        code_reply("pad()", first=1),
        code_reply("boss()", first=2),
        obsolete_reply("The hole position changed"),
        plan_reply("Pad the base", "Boss the arm", "Drill the smaller hole"),
        code_reply("drill_smaller()", first=3),
        suspect_reply(),
        verdict_reply("pass"),
    ], applies=[
        {"status": "applied", "created": ["PadBase"], "description": {}},
        {"status": "applied", "created": ["BossArm"], "description": {}},
        {"status": "applied", "created": ["DrillHole"], "description": {}},
    ])

    assert result.ok, result.reason
    assert _undo_calls(tools) == []
    assert tools.created == ["PadBase", "BossArm", "DrillHole"]


def test_a_revision_that_outruns_the_undo_stack_blocks_without_undoing_anything():
    """
    The same drop as above, but the live Document's own undo stack has
    already spilled past its `MaxUndoSize` and holds nothing to undo -- the
    run must block with that reason rather than undo part of the way back
    and leave the Document between two Plans.
    """
    result, events, llm, tools = drive([
        plan_reply("Pad the base", "Boss the arm", "Drill the hole"),
        code_reply("pad()", first=1),
        code_reply("boss()", first=2),
        obsolete_reply("The arm width changed"),
        plan_reply("Pad the base", "Boss the wider arm", "Drill the smaller hole"),
    ], applies=[
        {"status": "applied", "created": ["PadBase"], "description": {}},
        {"status": "applied", "created": ["BossArm"], "description": {}},
    ], undo_capacity=0)

    assert not result.ok
    assert result.blocked
    assert "undo" in result.reason.lower()
    assert _undo_calls(tools) == [], (
        "blocked but still undid part of the way: %r" % _undo_calls(tools))
    assert tools.created == ["PadBase", "BossArm"], (
        "the Document changed even though the run blocked: %r" % tools.created)
