"""
Marshals a callable onto the Qt main thread and blocks until it returns; the
engine's `ApplyPipeline` reaches Document operations through an injected
`dispatch` callable so `core` itself never touches Qt. The mechanism is a
queued signal to an object living in the main thread, not
`QTimer.singleShot(0, func)`, which creates its timer in the calling thread
and fires there instead on a worker thread with its own event loop. Firing on
the wrong thread is a Document mutation off the main thread, which is how
FreeCAD gets taken down.
"""
import contextlib
import threading

from core.freecad_apply import Closing

DISPATCH_TIMEOUT = 60.0

_dispatcher = None
_dispatcher_lock = threading.Lock()
_closing = threading.Event()
_stopping = 0
_stopping_lock = threading.Lock()
_waiting = set()


def _refusing():
    return _closing.is_set() or bool(_stopping)


def _release():
    for done in list(_waiting):
        done.set()


@contextlib.contextmanager
def stopping():
    """
    One Session's teardown: refuse and release for as long as it lasts.

    A run parked here waits on a job only the main thread's event loop can
    take and a teardown on that thread never pumps it, measured as 3.0 s of
    frozen window and then a SIGSEGV on a thread ended mid-Document. Releasing
    once is not enough, since a run told to stop is unwinding and asks again,
    and `_closing` stays unset so one Session's teardown does not refuse every
    other Session for the rest of the FreeCAD session.
    """
    global _stopping

    with _stopping_lock:
        _stopping += 1
    try:
        _release()
        yield
    finally:
        with _stopping_lock:
            _stopping -= 1


def closing():
    """
    Release every caller waiting on the main thread, and refuse new ones.

    The main thread takes these jobs from its event loop, and the panel's own
    shutdown runs on that thread without pumping it — so a worker parked here
    when FreeCAD is closing waits out `DISPATCH_TIMEOUT` for a job nobody will
    ever take, and is force-ended mid-mutation of the Document instead. That
    is FreeCAD hanging on exit.
    """
    _closing.set()
    _release()


def _qt():
    try:
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui as QtWidgets
    return QtCore, QtWidgets


class _Dispatcher:
    """
    Runs a callable on the Qt main thread and blocks until it returns. The
    mechanism is a queued signal to an object living in the main thread, not
    `QTimer.singleShot(0, func)`, which creates its timer in the calling
    thread and fires there instead on a worker thread with its own event
    loop -- a Document mutation off the main thread, which is how FreeCAD
    gets taken down.
    """

    def __init__(self):
        QtCore, QtWidgets = _qt()
        app = QtWidgets.QApplication.instance() or QtCore.QCoreApplication.instance()
        if app is None:
            raise RuntimeError("No Qt application; nothing to marshal onto.")

        class Relay(QtCore.QObject):
            job = QtCore.Signal(object)

            def __init__(self):
                super().__init__()
                self.moveToThread(app.thread())
                self.job.connect(self._run)

            @QtCore.Slot(object)
            def _run(self, run_it):
                run_it()

        self._app = app
        self._qtcore = QtCore
        self._relay = Relay()

    def on_main_thread(self, func, timeout=DISPATCH_TIMEOUT):
        """
        The caller is registered as waiting before it is refused, not after.

        A teardown beginning between the refusal check and the registration
        would find nothing to release, and this caller would wait out
        *timeout* instead: registering first closes that race, since the
        empty `outcome` a release leaves behind is what turns it into
        `Closing`.
        """
        QtCore = self._qtcore
        if QtCore.QThread.currentThread() == self._app.thread():
            return func()

        outcome = {}
        done = threading.Event()

        def run():
            try:
                outcome["value"] = func()
            except BaseException as exc:
                outcome["error"] = exc
            finally:
                done.set()

        _waiting.add(done)
        try:
            if _refusing():
                raise Closing("The Document operation was abandoned: FreeCAD is closing.")
            self._relay.job.emit(run)
            if not done.wait(timeout):
                raise TimeoutError(
                    "The Document operation did not return within %ss on the main thread."
                    % timeout
                )
        finally:
            _waiting.discard(done)
        if not outcome:
            raise Closing("The Document operation was abandoned: FreeCAD is closing.")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]


def on_main_thread(func, timeout=DISPATCH_TIMEOUT):
    """Marshal *func* onto the Qt main thread; run it here if we are already there."""
    global _dispatcher
    with _dispatcher_lock:
        if _dispatcher is None:
            _dispatcher = _Dispatcher()
    return _dispatcher.on_main_thread(func, timeout)
