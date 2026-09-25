"""Execute's first message already lists the Design model, so it does not
have to print `design` to read it back."""
import json

from core.prompts import read_prompt

from tests.loop_helpers import (
    A_PART, _execute_attempts, assertion_reply, code_reply, design_reply,
    drive, everything, plan_reply, suspect_reply, verdict_reply,
)

EXECUTE = read_prompt("execute.md")


def test_the_prompt_says_the_part_is_already_shown_before_a_code_turn():
    assert "what_the_part_is" in EXECUTE.split("## How you work", 1)[1]


def test_the_first_message_lists_every_feature_id_and_dimensional_number():
    _, _, llm, _ = drive([
        design_reply(A_PART),
        plan_reply("Drill the hole", satisfies=everything(A_PART)),
        code_reply("doc.addObject(\'Part::Box\', \'Box\')"),
        suspect_reply(),
        assertion_reply("assert True"),
        verdict_reply("pass"),
    ], applies=[{"status": "applied", "created": ["Box"], "description": {}}],
       intent="Drill a hole")

    shown = _execute_attempts(llm)[0]["what_the_part_is"]

    assert "hole-8-left" in shown
    assert "80 mm" in shown and "40 mm" in shown and "10 mm" in shown
    assert "15 mm" in shown
