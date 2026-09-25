"""
Reproduces a fillet body's own recompute against a real headless FreeCAD:
Steps 1-16 rebuild the ring's base body and must match its logged volume
within 1 mm3.

The later fillet steps run as a script, `ring_fillet_classification.py` in
this directory, because their combined recompute cost makes the
pytest-timeout plugin's wall clock unreliable in this environment.
"""
import json
import os

import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED_VOLUME_MM3 = 319904.85


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


def test_steps_1_to_16_rebuild_the_logged_volume(kernel):
    """AC: Steps 1-16 reproduce the log's body, volume within 1 mm3."""
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")

    boot = kernel.check("doc.addObject('PartDesign::Body', 'Body')", design=design)
    assert boot.ok, boot.traceback

    for n in range(1, 17):
        result = kernel.check(steps[str(n)])
        assert result.ok, "Step %d failed: %s" % (n, result.traceback)

    volume = kernel.check("print(doc.getObject('Body').Shape.Volume)")
    assert volume.ok, volume.traceback
    assert abs(float(volume.stdout.strip()) - EXPECTED_VOLUME_MM3) < 1.0
