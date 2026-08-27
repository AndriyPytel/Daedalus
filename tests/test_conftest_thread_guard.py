"""Test for the autouse fixture that fails a test which leaves a QThread alive.

Written first, against the fixture the fix adds to `tests/conftest.py`: a
leaked worker thread is what turned one flaky panel test into a whole-suite
deadlock, and the earlier failure mode was silence — no traceback, just a
frozen CPU. This drives a real pytest subprocess against a planted test that
starts a QThread, keeps a reference so it is not destroyed with the running
thread still attached (that abort is not the defect under test), and never
joins it — then asserts the run fails loudly, naming both the thread and the
test that leaked it, instead of hanging.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PLANTED_TEST = textwrap.dedent("""
    from conftest import QtCore, HAVE_QT
    import pytest
    import threading

    _kept_alive = []

    def test_a_thread_started_here_is_never_joined():
        if not HAVE_QT:
            pytest.skip("Qt is not available")

        class Never(QtCore.QThread):
            def run(self):
                threading.Event().wait()

        t = Never()
        _kept_alive.append(t)
        t.start()
        assert t.isRunning()
""")


def test_a_test_that_leaks_a_qthread_fails_loudly_instead_of_hanging(tmp_path):
    planted = ROOT / "tests" / "_planted_leak_for_test_conftest_thread_guard.py"
    planted.write_text(PLANTED_TEST)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(planted)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120,
        )
    finally:
        planted.unlink()

    assert result.returncode != 0, (
        "a test that leaks a running QThread must fail the suite, not pass it"
    )
    assert "test_a_thread_started_here_is_never_joined" in result.stdout
    assert "QThread" in result.stdout
