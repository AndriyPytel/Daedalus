"""
The saved session document replays the Assertion phase under a
bound measured here, not asserted by eye.

The session `kernel_checkpoint_wggws3z7.FCStd` the ticket measured against is
gone from `$TMPDIR` -- gone is what a kernel checkpoint always is once the run
that wrote it ends. A fixture of the same shape (a Box with three fillets)
stands in.
"""
import json
import os

import pytest

from core.events import EventStream
from core.kernel import Kernel, copy_of, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.loop.document import copied
from core.tools import ToolRegistry

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def a_box_with_three_fillets():
    """The shape of the session the ticket measured: 12 features, 3 fillets."""
    import FreeCAD

    doc = FreeCAD.newDocument("AssertionReplayFixture")
    box = doc.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 108, 30, 30
    doc.recompute()
    fillet = doc.addObject("Part::Fillet", "Fillet")
    fillet.Base = box
    fillet.Edges = [(edge, 3.0, 3.0) for edge in (1, 5, 9)]
    doc.recompute()
    try:
        yield copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


_REPLAY_BOUND_CALLS = 3
"""
Measured on this fixture (Box + 3 fillets, one acceptance criterion, a
`declare` on the first attempt): one `document_copy` to open the phase's own
copy and two `check_assertions` round trips, one for the fixed
self-intersection check and one for the declared attempt. Any run past that
is a retry the phase was not asked for or a fixed check run more than once,
neither of which the wall clock this replaced was measuring.
"""


def test_the_phase_replays_under_the_measured_bound(a_box_with_three_fillets):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    fixture_path = a_box_with_three_fillets
    checks_worker = Kernel()
    registry = ToolRegistry()
    calls = []
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: calls.append("document_copy") or {"path": copy_of_fixture(fixture_path)})
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: calls.append("check_assertions") or _check(checks_worker, fixture_path, code))

    llm = ScriptedLLMClient([json.dumps({"declare": {"checks": [{"entry": 0, "expr": "True"}]}})])
    events = EventStream()

    checked = assertion(["the part is a box with rounded edges"], {}, registry, llm, events)

    checks_worker.kill()

    assert checked.failed is False
    assert len(calls) <= _REPLAY_BOUND_CALLS, \
        "the Assertion phase made %d kernel calls, past the %d call bound: %r" % (
            len(calls), _REPLAY_BOUND_CALLS, calls)


def copy_of_fixture(fixture_path):
    import FreeCAD

    doc = FreeCAD.openDocument(fixture_path)
    try:
        return copy_of(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)


def _check(worker, fixture_path, code):
    copy = copy_of_fixture(fixture_path)
    try:
        result = worker.check(code, document=copy, ephemeral=True)
    finally:
        os.remove(copy)
    return {"ok": result.ok, "traceback": result.traceback,
           "held": result.held, "failed": result.failed,
           "defects": result.defects, "geometry": result.geometry}
