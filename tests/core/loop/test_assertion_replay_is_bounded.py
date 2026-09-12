"""
FCC-389: the saved session document replays the Assertion phase under a
bound measured here, not asserted by eye.

The session `kernel_checkpoint_wggws3z7.FCStd` the ticket measured against is
gone from `$TMPDIR` -- gone is what a kernel checkpoint always is once the run
that wrote it ends. A fixture of the same shape (a Box with three fillets)
stands in.
"""
import json
import os
import time

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


_REPLAY_BOUND_SECONDS = 5.0
"""
Measured on this fixture (Box + 3 fillets): one `document_copy`, one folded
envelope + self-intersection round trip and one attempt's `check_assertions`
round trip together ran 0.52s-0.63s across three runs, one `freecadcmd`
worker booted once. 5s leaves headroom for a slower CI worker boot without
hiding a regression back toward the 9m12s the ticket is about, which was
several boots and copies, not one.
"""


def test_the_phase_replays_under_the_measured_bound(a_box_with_three_fillets):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    fixture_path = a_box_with_three_fillets
    checks_worker = Kernel()
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": copy_of_fixture(fixture_path)})
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: _check(checks_worker, fixture_path, code))

    llm = ScriptedLLMClient([json.dumps({"declare": {"code": "assert True"}})])
    events = EventStream()

    started = time.time()
    checked = assertion(["the part is a box with rounded edges"], {}, registry, llm, events)
    elapsed = time.time() - started

    checks_worker.kill()

    assert checked.failed is False
    print("ASSERTION_REPLAY_ELAPSED=%.3f" % elapsed)
    assert elapsed < _REPLAY_BOUND_SECONDS, \
        "the Assertion phase took %.2fs, past the %ss bound" % (elapsed, _REPLAY_BOUND_SECONDS)


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
           "held": result.held, "failed": result.failed, "geometry": result.geometry}
