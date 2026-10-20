"""Where nobody answers, Plan is handed the drawing beside the description; where a person does, it is not."""
import pytest

from tests.loop_helpers import (
    RENDER_PNG,
    A_DATUM,
    a_person_who_declines,
    code_reply,
    design_reply,
    drive,
    plan_reply,
    prompts_of,
    suspect_reply,
    verdict_reply,
)


@pytest.mark.parametrize("ask, pictures", [(None, True), (a_person_who_declines, False)])
def test_plan_is_given_the_pictures_only_where_nobody_answers(ask, pictures):
    _, _, llm, _ = drive([design_reply(A_DATUM), design_reply(), plan_reply("Add a box"),
                          code_reply("box()"), suspect_reply(), verdict_reply("pass")],
                         attachments=[RENDER_PNG], ask=ask)

    plan_calls = [call for call in llm.calls if call[0]["content"].startswith(prompts_of("plan"))]
    content = plan_calls[0][-1]["content"]
    shown = [block for block in content if block.get("type") == "image_url"] \
        if isinstance(content, list) else []

    assert bool(shown) is pictures
