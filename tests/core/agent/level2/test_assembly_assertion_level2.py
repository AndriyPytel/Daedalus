"""
Level 2 -- the Assembly Assertion measured on real Assemblies solved by FreeCAD's own solver, headless.
"""
import pytest

from core.agent.assembly import measured, refusals, unfinished
from core.kernel import Kernel, find_freecadcmd
from tests.assembly_helpers import PARTS, SOLVED

pytestmark = pytest.mark.freecad

CASES = {
    "no ground": (PARTS + "fixed(a, b, (10, 0, 0))\ndoc.recompute()\n", "no grounded part"),
    "no joint": (PARTS + "ground(a)\ndoc.recompute()\n", "no joint"),
    "overlap": (PARTS + "ground(a)\nfixed(a, b, (5, 0, 0))\ndoc.recompute()\n", "interfere"),
    "over-constrained": (PARTS + "ground(a)\nfixed(a, b, (10, 0, 0))\nfixed(a, b, (20, 0, 0))\ndoc.recompute()\n",
                         "over-constrained"),
    "unjoined part": (PARTS + "c = box('C', (60, 0, 0))\nground(a)\nfixed(a, b, (10, 0, 0))\ndoc.recompute()\n",
                      "not joined"),
    "no assembly": ("import Part\ndoc.addObject('Part::Box', 'Box')\n", "no Assembly::AssemblyObject"),
}


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def _built(kernel, code):
    assert kernel.check(code).ok


def test_a_solved_assembly_with_a_joint_and_clear_parts_passes(kernel):
    _built(kernel, SOLVED)

    facts = measured(kernel)

    assert refusals(facts) == [], facts
    assert unfinished(kernel) is None


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_way_to_fail_is_refused_with_its_reason(kernel, name):
    code, reason = CASES[name]
    _built(kernel, code)

    said = unfinished(kernel)

    assert said is not None and reason in said, said


def test_asking_does_not_move_the_agents_assembly(kernel):
    _built(kernel, SOLVED + "print(b.Placement.Base.x)")
    placed = kernel.check("print(doc.getObject('B').Placement.Base.x)", exploring=True).stdout.strip()

    unfinished(kernel)

    assert kernel.check("print(doc.getObject('B').Placement.Base.x)", exploring=True).stdout.strip() == placed
