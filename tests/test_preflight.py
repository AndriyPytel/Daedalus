"""
Level 1 — the parent half of Preflight, with no FreeCAD in play.

Everything here is about the reader threads: what they decode, and what they
are obliged to do on the way out. The worker itself is Level 2
(`test_preflight_level2.py`); these are the two threads that stand between it
and the caller.
"""
import queue
import time
from types import SimpleNamespace

import pytest

from core.preflight import PreflightWorker


class _StdoutThatDiesMidStream:
    """A stdout whose decoder gives up part way, the way an undecodable byte does."""

    def __iter__(self):
        yield "FreeCAD banner, not a response\n"
        raise UnicodeDecodeError("ascii", b"\xd0", 0, 1, "ordinal not in range(128)")


@pytest.fixture
def unstarted():
    """A worker object with no process behind it; the threads are driven by hand."""
    worker = PreflightWorker(freecadcmd="/nonexistent/freecadcmd")
    worker._lines = queue.Queue()
    return worker


def test_a_reader_that_dies_still_marks_the_end_of_the_stream(unstarted):
    """
    `_pump` owns the only "the worker is gone" signal there is. If it dies
    without sending it, the queue stays empty and `_await_response` waits out
    its full timeout for a worker that will never speak again. FCC-51.
    """
    unstarted._process = SimpleNamespace(stdout=_StdoutThatDiesMidStream())

    with pytest.raises(UnicodeDecodeError):
        unstarted._pump()

    assert unstarted._lines.get_nowait() is None, \
        "the reader died without marking the end of the stream"


def test_a_request_pending_on_a_dead_reader_fails_instead_of_waiting(unstarted):
    """
    The consequence of the above, at the seam the caller actually sits on.

    `is None` alone would pass either way — waiting out the timeout also ends
    in None. The elapsed time is the whole assertion.
    """
    unstarted._process = SimpleNamespace(stdout=_StdoutThatDiesMidStream())

    try:
        unstarted._pump()
    except UnicodeDecodeError:
        pass

    started = time.monotonic()
    response = unstarted._await_response(timeout=30)
    elapsed = time.monotonic() - started

    assert response is None
    assert elapsed < 1, "the caller waited out the timeout for a worker already gone"
