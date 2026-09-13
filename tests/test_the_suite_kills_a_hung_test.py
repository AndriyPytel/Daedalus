"""
A test that never returns must fail the suite loudly instead of hanging it.

`faulthandler_timeout` only dumps a traceback and lets a wedged test keep
waiting, so under `pytest-xdist` its worker never dies and the controller
waits forever on a report that never comes; `pytest-timeout` with the thread
method force-exits that worker, which xdist reports as a named crash. The
planted test carries its own short `timeout` marker and the run caps the
worker-restart retries, so the mechanism is proven without waiting on the
suite's real 300-second ceiling.
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
