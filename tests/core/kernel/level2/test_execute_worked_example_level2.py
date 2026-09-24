"""
Level 2 -- `execute.md`'s worked Step example, replayed against a real
kernel: linting an example proves nothing about whether the FreeCAD API
calls in it actually work, only that its shape avoids what the lint refuses.
"""
import re

import pytest

from core.kernel import Kernel, find_freecadcmd
from core.prompts import read_prompt

pytestmark = pytest.mark.freecad


@pytest.fixture
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


DESIGN = {"features": {"Boss": {"length": {"mm": 80}, "width": {"mm": 40},
                                "height": {"mm": 24}}}}

BODY_SETUP = "body = doc.addObject('PartDesign::Body', 'Body')\ndoc.recompute()\n"


def _worked_example():
    blocks = re.findall(r"```python\n(.*?)```", read_prompt("execute.md"), re.DOTALL)
    assert len(blocks) == 1
    return blocks[0]


def test_the_worked_example_builds_one_solid_of_the_right_size(worker):
    code = (BODY_SETUP + _worked_example()
            + "held = [len(pad.Shape.Solids), box.XLength, box.YLength, box.ZLength]\n")

    result = worker.check(code, None, design=DESIGN)

    assert result.ok, result.traceback
    solids, x_length, y_length, z_length = result.held
    assert solids == 1
    assert x_length == pytest.approx(80, abs=1e-6)
    assert y_length == pytest.approx(40, abs=1e-6)
    assert z_length == pytest.approx(24, abs=1e-6)
