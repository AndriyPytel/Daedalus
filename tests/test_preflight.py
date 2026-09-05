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

from core.kernel import Kernel


class _StdoutThatDiesMidStream:
    """A stdout whose decoder gives up part way, the way an undecodable byte does."""

    def __iter__(self):
        yield "FreeCAD banner, not a response\n"
        raise UnicodeDecodeError("ascii", b"\xd0", 0, 1, "ordinal not in range(128)")


@pytest.fixture
def unstarted():
    """A worker object with no process behind it; the threads are driven by hand."""
    worker = Kernel(freecadcmd="/nonexistent/freecadcmd")
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


class _AProcessThatTakesTheRequest:
    """A live process that accepts a write and never answers."""

    def __init__(self):
        self.stdin = SimpleNamespace(write=lambda _: None, flush=lambda: None)

    def poll(self):
        return None


def test_a_worker_that_cannot_come_back_answers_no_reading_instead_of_raising(
        unstarted, monkeypatch):
    """
    FCC-126 moved the reading check into the child so a segfaulting solver
    costs the reading and not the run. A restart that cannot be made was the
    hole in that: `start()` raised out of `reading()`, through
    `_the_geometry_disagrees`, and ended the run at the second death.
    """
    monkeypatch.setattr(Kernel, "start", _never_comes_up)

    assert unstarted.reading({"do": "reading"}, timeout=0.1) is None


def test_a_restart_that_fails_after_a_death_is_a_result_and_not_an_exception(
        unstarted, monkeypatch):
    """
    The same promise on the Step path: whatever happened, the caller gets a
    KernelResult back. Here the request goes down the pipe, the worker never
    answers, and the replacement cannot be started either.
    """
    unstarted._process = _AProcessThatTakesTheRequest()
    unstarted._lines.put(None)
    monkeypatch.setattr(Kernel, "start", _never_comes_up)

    result = unstarted.check("pad()", timeout=0.1)

    assert result.ok is False
    assert result.traceback


def _never_comes_up(*_, **__):
    raise RuntimeError("Preflight Worker did not come up: ")


def test_a_truncated_json_line_is_a_dead_worker_not_a_raised_exception(unstarted):
    """
    A worker can die mid-write of its own response — `respond()`'s
    `sys.stdout.write` gets cut off without the trailing newline, most often
    by a segfault inside the FreeCAD core. `_await_response` must treat that
    the same way it treats a dead reader: a result, not a `JSONDecodeError`
    escaping the "never raises" contract. FCC-275.
    """
    unstarted._lines.put('{"ok": true, "featur')

    assert unstarted._await_response(timeout=1) is None


class _AProcessThatDiesMidResponse:
    """Alive for the liveness check, dead by the time the reply is read."""

    def __init__(self):
        self.stdin = SimpleNamespace(write=lambda _: None, flush=lambda: None)
        self._polls = 0
        self.killed = False

    def poll(self):
        self._polls += 1
        return None if self._polls == 1 else -11

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -11


def test_a_truncated_stdout_line_comes_back_from_check_as_a_dead_worker(unstarted):
    """
    The same crash as above, driven through the public `check()` rather than
    `_await_response` directly — the entrypoint the "never raises" contract
    is actually made to (FCC-275 AC2). No `JSONDecodeError` escapes, and the
    dead process is killed rather than left running with a reader thread
    still attached to it.
    """
    process = _AProcessThatDiesMidResponse()
    unstarted._process = process
    unstarted._lines.put('{"ok": true, "featur')

    result = unstarted.check("pad()", timeout=1)

    assert result.ok is False
    assert result.traceback
    assert process.killed, "the dead process was left running"
    assert unstarted._process is None, "no live process was left behind"
