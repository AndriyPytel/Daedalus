"""Execute is handed the drawing, and where nobody answers the part is compared with it once, after the last Step."""
import json

from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from core.loop.gate import nobody_to_ask
from tests.loop_helpers import (
    RENDER_PNG,
    FakeTools,
    StubKernel,
    a_person_who_declines,
    code_reply,
    design_reply,
    drive,
    nothing_to_add,
    plan_reply,
    prompts_of,
    suspect_reply,
    verdict_reply,
)


def test_execute_puts_the_drawing_before_its_request():
    llm = ScriptedLLMClient(code_reply("pad()"))
    execute(["Pad the outline"], FakeTools().registry, llm, EventStream(), kernel=StubKernel(),
            attachments=[RENDER_PNG])

    content = llm.calls[0][-1]["content"]
    assert any(block.get("type") == "image_url" for block in content)
    assert "Pad the outline" in content[-1]["text"]


def _run(ask, replies):
    return drive([design_reply(), plan_reply("Pad the outline 10 mm"), code_reply("pad()"), *replies],
                 attachments=[RENDER_PNG], ask=ask)


def test_the_part_is_compared_with_the_drawing_once_where_nobody_answers():
    result, _, llm, tools = _run(nobody_to_ask, [
        json.dumps({"code": "boss()", "step": 2, "label": "Boss"}),
        json.dumps({"declare": {"steps": [{"step": 2, "label": "Boss"}]}})])

    assert result.ok and result.steps_applied == 2
    assert tools.applied_codes() == ["pad()", "boss()"]
    comparisons = [call for call in llm.calls if "Compare each view" in json.dumps(call[-1]["content"])]
    assert len(comparisons) == 1


def test_a_person_at_the_gate_gets_no_comparison():
    _, _, llm, _ = _run(a_person_who_declines, [suspect_reply(), verdict_reply("pass")])

    assert not [call for call in llm.calls if "Compare each view" in json.dumps(call[-1]["content"])]


def test_only_the_comparison_turn_carries_the_drawing_among_the_execute_turns():
    _, _, llm, _ = _run(nobody_to_ask, [nothing_to_add()])

    opening = [call[1]["content"] for call in llm.calls
               if len(call) == 2 and call[0]["content"].startswith(prompts_of("execute"))]
    pictured = [isinstance(content, list) and any(b.get("type") == "image_url" for b in content)
                for content in opening]
    assert pictured == [False, True]


def test_the_comparison_is_told_not_to_name_a_blanket_round_or_a_feature_already_built():
    from core.prompts import read_prompt

    said = " ".join(read_prompt("compare_with_the_drawing.md").split())
    assert "Do not name a fillet or chamfer over all remaining, all concave or all other edges" in said
    assert "never one already built" in said
