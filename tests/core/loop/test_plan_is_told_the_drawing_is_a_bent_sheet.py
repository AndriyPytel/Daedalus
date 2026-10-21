"""Where nobody answers, a description that mentions a flat pattern makes Plan plan the formed part."""
import pytest

from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.plan import plan
from core.nobody import nobody_answering
from tests.loop_helpers import StubKernel, plan_reply

SHEET = {"features": [{"id": "pad-outer", "kind": "pad",
                       "length": {"value": "120 mm (flat pattern)", "flag": "read", "mm": 120}}]}
BLOCK = {"features": [{"id": "pad-outer", "kind": "pad",
                       "length": {"value": "120 mm", "flag": "read", "mm": 120}}]}


def _asked(part, nobody):
    llm = ScriptedLLMClient([plan_reply("Pad the plate 5 mm")])
    if nobody:
        with nobody_answering():
            plan("x", {}, llm, EventStream(), the_part=part, kernel=StubKernel())
    else:
        plan("x", {}, llm, EventStream(), the_part=part, kernel=StubKernel())
    return llm.calls[0][-1]["content"]


@pytest.mark.parametrize("part, nobody, present",
                         [(SHEET, True, True), (BLOCK, True, False), (SHEET, False, False)])
def test_the_bent_sheet_field_is_present_only_for_a_flat_pattern_where_nobody_answers(
        part, nobody, present):
    assert ("the_drawing_is_a_bent_sheet" in _asked(part, nobody)) is present


class _Dial(ScriptedLLMClient):
    effort = "medium"
    seen = []

    def stream(self, messages, **overrides):
        self.seen.append(self.effort)
        return super().stream(messages, **overrides)


@pytest.mark.parametrize("part, effort", [(BLOCK, "low"), (SHEET, "medium")])
def test_plan_runs_at_low_effort_unless_the_drawing_is_a_bent_sheet(part, effort):
    llm = _Dial([plan_reply("Pad the plate 5 mm")])
    llm.seen.clear()
    with nobody_answering():
        plan("x", {}, llm, EventStream(), the_part=part, kernel=StubKernel())

    assert llm.seen == [effort]
