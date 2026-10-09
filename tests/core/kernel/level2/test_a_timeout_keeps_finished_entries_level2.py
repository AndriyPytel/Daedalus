"""Level 2 -- a real kernel killed by its timeout in the middle of an expression leaves the finished entries and the entry in flight in the progress file."""
import pytest

from core.kernel import TIMED_OUT, Kernel, find_freecadcmd
from core.loop.expressions import each_expression_evaluated, progress_read
from tests.core.kernel.level2.test_mechanical_checks_level2 import _PLATE, _DATUMS

pytestmark = pytest.mark.freecad


def test_an_expression_that_sleeps_past_the_timeout_costs_its_own_entry_only(tmp_path):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    progress = str(tmp_path / "progress.jsonl")
    expressions = {0: "True", 1: "False", 2: "no_such_name", 3: "__import__('time').sleep(60) or True", 4: "True"}
    with Kernel(timeout=5) as worker:
        assert worker.check(_PLATE % "", None, design=_DATUMS).ok

        result = worker.check(each_expression_evaluated(expressions, progress))

    assert not result.ok and TIMED_OUT in result.traceback
    held, failed, defects, in_flight = progress_read(progress)
    assert held == [0] and failed == [1] and [entry for entry, _ in defects] == [2]
    assert in_flight == 3
