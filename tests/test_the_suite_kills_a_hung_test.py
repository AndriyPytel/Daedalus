"""
Test for the suite-wide guarantee that a test which never returns fails
loudly instead of hanging the whole run.

Written first, against the fix `pytest.ini` adds: `faulthandler_timeout`
only dumps a traceback and lets a wedged test keep running forever
(verified directly against a planted, genuinely unblockable wait), so under
`pytest-xdist` the worker sitting on it never crashes and the controller
waits on its report with nothing to time it out -- the silent, whole-suite
hang FCC-197 reports. `pytest-timeout` with the thread method force-exits
the wedged worker, which xdist then reports as a named, attributed crash
instead of silence. The planted test carries its own short `timeout` marker,
and the run caps xdist's worker-restart retries, so this proves the
mechanism without waiting on the suite's real 300-second ceiling or on
xdist's full retry budget.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PLANTED_TEST = textwrap.dedent("""
    import pytest
    import threading

    @pytest.mark.timeout(3, method="thread")
    def test_a_wait_nothing_can_ever_release():
        threading.Event().wait()
""")


def test_a_test_that_never_returns_fails_loudly_instead_of_hanging():
    planted = ROOT / "tests" / "_planted_unblockable_wait_for_test_the_suite_kills_a_hung_test.py"
    planted.write_text(PLANTED_TEST)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--max-worker-restart=1", str(planted)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=90,
        )
    finally:
        planted.unlink()

    assert result.returncode != 0, (
        "a test that never returns must fail the suite, not pass or hang it"
    )
    assert "test_a_wait_nothing_can_ever_release" in result.stdout
