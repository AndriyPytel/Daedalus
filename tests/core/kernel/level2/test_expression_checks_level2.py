"""Level 2 -- expression checks evaluated one at a time in the real check namespace, on the 60 x 40 x 10 plate and on part 202."""
import pytest

from core.kernel import Kernel, find_freecadcmd
from core.loop.expressions import each_expression_evaluated
from tests.core.kernel import part_202
from tests.core.kernel.level2.test_mechanical_checks_level2 import _PLATE, _DATUMS

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_one_expression_raising_leaves_every_other_entry_with_its_result(worker):
    assert worker.check(_PLATE % "", None, design=_DATUMS).ok
    expressions = {
        0: "abs(distance(cylinder(6, 'z'), face_at('-x')) - 20) <= 0.5",
        1: "abs(distance(cylinder(6, 'z'), face_at('-x')) - 99) <= 0.5",
        2: "abs(distance(cylinder(7, 'z'), face_at('-x')) - 20) <= 0.5",
        3: "abs(distance(cylinder(10, 'z'), cylinder(6, 'z')) - 25) <= 0.5",
        4: "len([1, 2])",
    }

    result = worker.check(each_expression_evaluated(expressions))

    assert result.ok, result.traceback
    assert result.held == [0, 3] and result.failed == [1]
    assert sorted(entry for entry, _ in result.defects) == [2, 4]
    assert "found 0" in dict(result.defects)[2]


def test_expressions_run_on_the_part_202_fixture():
    import FreeCAD

    from core.kernel import kernel_worker

    document = part_202.opened()
    try:
        namespace = kernel_worker.step_namespace(document, design={"datums": []})
        script = each_expression_evaluated({
            0: "face_at('+z').Area > 0",
            1: "abs(distance(face_at('+z'), face_at('+z'))) <= 0.5",
            2: "no_such_helper(1)",
        })
        exec(compile(script, "<expressions>", "exec"), namespace)
    finally:
        FreeCAD.closeDocument(document.Name)

    assert namespace["held"] == [0, 1] and namespace["failed"] == []
    assert [entry for entry, _ in namespace["defects"]] == [2]
