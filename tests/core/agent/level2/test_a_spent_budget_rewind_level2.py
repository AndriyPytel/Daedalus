"""
Level 2 -- a Step whose retry budget runs out against a real kernel
subprocess and a real FreeCAD Document: `on_declaration` giving up is as
much a non-delivery as `retry` or `drift`, and the kernel's own Preflight
session has to be rewound the same way, or the very next turn against that
same session still sees what the abandoned attempt created.
"""
import json

import pytest

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.events import TOOL_RESULT, EventStream
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient
from tests.event_helpers import events_of

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


@pytest.fixture
def empty_document(tmp_path):
    import FreeCAD

    path = str(tmp_path / "empty.FCStd")
    document = FreeCAD.newDocument("SpentBudgetLevel2")
    try:
        document.saveAs(path)
    finally:
        FreeCAD.closeDocument(document.Name)
    return path


def test_the_next_turn_after_a_spent_budget_sees_no_debris_from_it(tmp_path, worker, empty_document):
    stream = EventStream()

    def gave_up(declaration):
        return {"delivered": False, "traceback": "would not land"}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1}),
        json.dumps({"code": "assert doc.Objects == [], 'debris survived a spent budget'\n"
                            "doc.addObject('Part::Box', 'Box')"}),
        json.dumps({"declare": {"built": "nothing, just proving the rehearsal session is clean"}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
             document=empty_document, kernel=worker, on_declaration=gave_up, stream=stream)

    checked = [event.data["result"] for event in events_of(stream, TOOL_RESULT)]
    assert checked[1]["ok"], (
        "the turn after Step 1's spent budget still saw its debris: %s" % checked[1].get("traceback"))
