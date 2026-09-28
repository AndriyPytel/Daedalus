"""
Level 2 -- Plan's frozen values reach Execute through the real kernel, the
same session, across a document handoff and a worker restart, no prose
number ever retyped.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_value_plan_computes_is_read_by_name_in_a_later_step(kernel):
    """Plan's own code computes arm_y; a later Step reads plan_values['arm_y']."""
    computed = kernel.check("arm_y = 65.5 - 92.63 * __import__('math').cos("
                            "__import__('math').radians(45))",
                            document=None, capture_values=True, exploring=True)
    assert computed.ok, computed.traceback
    assert computed.plan_values["arm_y"] == pytest.approx(0.0006988586901002236)

    used = kernel.check(
        "doc.addObject('Part::Box', 'Box').Length = plan_values['arm_y'] + 100")

    assert used.ok, used.traceback


def test_plan_values_survive_a_worker_restart(kernel):
    """The same restart-survival design.py already has, for the numbers Plan froze."""
    kernel.check("pass", document=None, ephemeral=True)
    kernel.check("arm_y = 46.458", capture_values=True, exploring=True)
    kernel.restart()

    result = kernel.check("assert plan_values['arm_y'] == 46.458")

    assert result.ok, result.traceback
