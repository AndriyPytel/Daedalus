"""
The main-thread dispatch, against a real Qt event loop.

This is the mechanism #1 called out as broken: the old core probed for
`FreeCADGui.addTodo`, which does not exist in 1.0 or 1.1, silently fell
through to `QTimer.singleShot(0, func)`, and nobody designed the fallback.
That fallback creates the timer in the *calling* thread, so from a worker
thread with its own event loop — which is exactly what the panel gave it — it
fires on the worker thread. A Document mutation off the main thread is how
FreeCAD dies.

So the replacement is tested for the property that actually matters: the
callable runs on the main thread, no matter which thread asked.

MAKES_A_DOCUMENT enumerates every module of `core` that calls `newDocument`,
and the process it calls it in. Two are the Preflight child (ADR-0002); the
third probes a type in a hidden, temporary Document and is recorded here
rather than fixed, so that it is not rediscovered as a surprise.
"""
import threading

import pytest

import core.apply


@pytest.fixture
def qt_app():
    from conftest import NO_QT, QtCore

    if QtCore is None:
        pytest.skip(NO_QT)

    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    core.apply._dispatcher = None
    try:
        yield app, QtCore
    finally:
        core.apply._dispatcher = None


def run_off_thread(job, qt_app, timeout=10):
    """Call *job* on a worker thread while the main thread pumps Qt events."""
    app, QtCore = qt_app
    outcome = {}
    done = threading.Event()

    def worker():
        try:
            outcome["value"] = job()
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while not done.is_set() and deadline.elapsed() < timeout * 1000:
        app.processEvents()
    thread.join(timeout=1)

    if "error" in outcome:
        raise outcome["error"]
    assert done.is_set(), "the worker never finished"
    return outcome["value"]


def test_work_asked_for_on_a_worker_thread_runs_on_the_main_thread(qt_app):
    app, QtCore = qt_app
    main = QtCore.QThread.currentThread()

    ran_on = run_off_thread(
        lambda: core.apply.on_main_thread(lambda: QtCore.QThread.currentThread()),
        qt_app,
    )

    assert ran_on == main


def test_the_worker_thread_blocks_until_the_document_operation_returns(qt_app):
    order = []

    def slow_document_operation():
        order.append("document")
        return "done"

    def on_worker():
        result = core.apply.on_main_thread(slow_document_operation)
        order.append("worker resumed")
        return result

    assert run_off_thread(on_worker, qt_app) == "done"
    assert order == ["document", "worker resumed"]


def test_a_failure_on_the_main_thread_is_re_raised_at_the_caller(qt_app):
    def explode():
        raise ValueError("the C++ core said no")

    with pytest.raises(ValueError, match="the C\\+\\+ core said no"):
        run_off_thread(lambda: core.apply.on_main_thread(explode), qt_app)


def test_already_on_the_main_thread_is_a_straight_call(qt_app):
    app, QtCore = qt_app

    ran_on = core.apply.on_main_thread(lambda: QtCore.QThread.currentThread())

    assert ran_on == QtCore.QThread.currentThread()


def test_what_the_user_selected_is_read_on_the_main_thread(qt_app):
    """
    `FreeCADGui.Selection` is the Gui's own state, and the `snapshot` tool
    reads it at the start of every run and again on every Step -- from the
    worker thread, where every other Gui touch in `apply()` is already
    marshalled. Read there directly it starts Gui-internal timers off the main
    thread, which is what `QBasicTimer::start: Timers cannot be started from
    another thread` is saying, and the window goes some widget update later.
    """
    import sys
    import types

    from core import freecad_tools

    app, QtCore = qt_app
    read_on = []
    gui = types.ModuleType("FreeCADGui")

    class _Selection:
        @staticmethod
        def getSelectionEx():
            read_on.append(QtCore.QThread.currentThread())
            return []

    gui.Selection = _Selection
    saved = sys.modules.get("FreeCADGui")
    sys.modules["FreeCADGui"] = gui
    try:
        run_off_thread(freecad_tools._selection, qt_app)
    finally:
        sys.modules.pop("FreeCADGui")
        if saved is not None:
            sys.modules["FreeCADGui"] = saved

    assert read_on == [QtCore.QThread.currentThread()]


def test_the_active_body_is_read_on_the_main_thread(qt_app):
    """
    The other unmarshalled Gui read the `snapshot` tool makes, for the same
    reason: `ActiveDocument.ActiveView` is the Gui's, not the Document's.
    """
    import sys
    import types

    from core import freecad_tools

    app, QtCore = qt_app
    read_on = []
    gui = types.ModuleType("FreeCADGui")

    class _View:
        @staticmethod
        def getActiveObject(name):
            read_on.append(QtCore.QThread.currentThread())
            return None

    gui.ActiveDocument = type("_Doc", (), {"ActiveView": _View})
    saved = sys.modules.get("FreeCADGui")
    sys.modules["FreeCADGui"] = gui
    try:
        run_off_thread(lambda: freecad_tools._active_body(type("_D", (), {"Objects": []})),
                       qt_app)
    finally:
        sys.modules.pop("FreeCADGui")
        if saved is not None:
            sys.modules["FreeCADGui"] = saved

    assert read_on == [QtCore.QThread.currentThread()]


def test_there_is_no_addtodo_or_singleshot_path_left_anywhere():
    """
    The dead branch is gone, not merely deprioritised.

    Checked against the parsed source rather than the text, so the comment
    explaining why they are gone does not count as using them. The panel's own
    scroll-to-bottom is Qt UI work on the main thread, not dispatch; only core/
    may not use these at all.
    """
    import ast
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(core.apply.__file__)))
    offenders = []
    for package in ("core", "ui", "Mod", "utils"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, package)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute) and node.attr in ("addTodo", "singleShot"):
                        offenders.append("%s uses %s" % (filename, node.attr))

    assert [o for o in offenders if not o.startswith("copilot_panel.py")] == []


def _core_sources():
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(core.apply.__file__)))
    here = os.path.join(root, "core")
    return [(name, os.path.join(here, name))
            for name in sorted(os.listdir(here)) if name.endswith(".py")]


MAKES_A_DOCUMENT = {
    "preflight_worker.py": "is the child",
    "sketch_check.py": "runs in the child",
    "freecad_tools.py": "hidden=True, temp=True, and known — FCC-126",
}


def test_only_the_child_makes_a_document():
    """
    The rule `core/apply.py` states, checked against the source rather than
    trusted: a Document mutation off the main thread is how FreeCAD gets taken
    down, and the loop's phases all run on the worker thread. FCC-122 added
    `sketch_check` to `_describe_the_part` and the window disappeared mid-run.

    An allowlist, so a new caller has to come and argue with this test.
    """
    import ast

    found = set()
    for name, path in _core_sources():
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "newDocument":
                found.add(name)

    assert found <= set(MAKES_A_DOCUMENT), \
        "these make a Document in the addon's own process: %s" % sorted(
            found - set(MAKES_A_DOCUMENT))


def test_the_loop_never_builds_the_reading_itself():
    """
    `sketch_check.checked` is the child's entry point. The loop's is
    `checked_by`, which sends the outline down the pipe — and the difference
    between them is the FreeCAD window staying open.
    """
    import ast
    import os

    path = os.path.join(os.path.dirname(os.path.abspath(core.apply.__file__)), "loop.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)

    called = [node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name)
              and node.func.value.id == "sketch_check"]

    assert "checked" not in called, "the loop builds the reading in its own process"


def _install_fake_freecad_console(tmp_path):
    """
    Swap in a `FreeCAD` whose `Console` records the calling thread, and force
    `utils.logger` to configure itself against it from scratch.

    `logging.getLogger("FreeCadCopilot")` is a process-wide singleton and
    `get_logger` only attaches handlers once, so a bare re-import after the
    real FreeCAD already configured it would silently keep the old handlers.
    Clearing the handler list and the module cache is what makes the swap
    take.
    """
    import logging
    import sys
    import types

    from conftest import QtCore

    calls = []

    class _Console:
        @staticmethod
        def PrintError(msg):
            calls.append(("error", msg, QtCore.QThread.currentThread()))

        @staticmethod
        def PrintMessage(msg):
            calls.append(("message", msg, QtCore.QThread.currentThread()))

    fake_freecad = types.ModuleType("FreeCAD")
    fake_freecad.Console = _Console
    fake_freecad.getUserAppDataDir = lambda: str(tmp_path) + "/"

    saved_freecad = sys.modules.get("FreeCAD")
    saved_logger_module = sys.modules.pop("utils.logger", None)
    logging.getLogger("FreeCadCopilot").handlers.clear()
    sys.modules["FreeCAD"] = fake_freecad

    def restore():
        sys.modules.pop("utils.logger", None)
        logging.getLogger("FreeCadCopilot").handlers.clear()
        if saved_freecad is not None:
            sys.modules["FreeCAD"] = saved_freecad
        else:
            sys.modules.pop("FreeCAD", None)
        if saved_logger_module is not None:
            sys.modules["utils.logger"] = saved_logger_module

    return calls, restore


def test_a_log_line_from_a_worker_thread_reaches_freecad_console_on_the_main_thread(qt_app, tmp_path):
    """
    The crash this addon actually hit: `LoopWorker.do_work` runs on a QThread
    and logs an ERROR event from it, `_FreeCADHandler.emit` called
    `FreeCAD.Console.PrintError` straight from there, and FreeCAD's
    Notification Area -- a Qt observer of the console that builds a widget
    tree and an auto-hide QTimer per message -- built them off its own
    thread. Sixteen `QObject::setParent: ... different thread` warnings, one
    `QObject::killTimer: ... another thread` warning, then a segfault.
    """
    app, QtCore = qt_app
    main = QtCore.QThread.currentThread()
    calls, restore = _install_fake_freecad_console(tmp_path)
    try:
        import utils.logger as ul
        run_off_thread(lambda: ul.logger.error("boom"), qt_app)
    finally:
        restore()

    assert len(calls) == 1
    level, msg, thread = calls[0]
    assert level == "error"
    assert "boom" in msg
    assert thread == main


def test_logging_with_no_qt_application_prints_directly_instead_of_raising(tmp_path):
    """
    `on_main_thread` raises `RuntimeError` when there is no Qt application at
    all -- Level 1, headless, `freecadcmd`. There is no GUI there to corrupt,
    so the handler falls back to calling `FreeCAD.Console` straight away
    instead of propagating the error out of a `logger.error(...)` call.
    """
    import core.apply

    calls, restore = _install_fake_freecad_console(tmp_path)

    def _no_qt_application(func, timeout=None):
        raise RuntimeError("No Qt application; nothing to marshal onto.")

    saved_dispatch = core.apply.on_main_thread
    core.apply.on_main_thread = _no_qt_application
    try:
        import utils.logger as ul
        ul.logger.error("boom")
    finally:
        core.apply.on_main_thread = saved_dispatch
        restore()

    assert len(calls) == 1
    level, msg, thread = calls[0]
    assert level == "error"
    assert "boom" in msg


def test_a_line_the_main_thread_never_takes_is_dropped(tmp_path):
    """
    `CopilotPanel.shut_down` waits on the worker thread from the main one and
    the worker emits `blocked` on its way out of a Stop, so the marshalling
    can time out at exactly the moment a line is written. Writing it from
    here anyway is the cross-thread `FreeCAD.Console` call the guard exists to
    prevent, and the `FileHandler` already has the line, so it is dropped.
    """
    import core.apply

    calls, restore = _install_fake_freecad_console(tmp_path)

    def _never_comes_back(func, timeout=None):
        raise TimeoutError(
            "The Document operation did not return within %ss on the main thread." % timeout)

    saved_dispatch = core.apply.on_main_thread
    core.apply.on_main_thread = _never_comes_back
    written = []
    try:
        import utils.logger as ul
        ul._console(written.append, "boom")
    finally:
        core.apply.on_main_thread = saved_dispatch
        restore()

    assert written == []
    assert calls == []


def test_a_dispatch_that_fails_some_other_way_does_not_escape_the_log_call(tmp_path):
    """
    `on_main_thread` re-raises whatever the write raised on the main thread,
    and `_qt` raises `ImportError` when neither PySide binding resolves.
    Neither is a `RuntimeError` or a `TimeoutError`, `Logger.callHandlers`
    does not wrap `emit`, and the one `logger.error` this addon makes outside
    `EventStream.emit`'s guard runs while `LoopWorker.do_work` is already
    handling a crash -- so an escape there costs the `finished` signal and
    the panel never learns the run ended.
    """
    import core.apply

    calls, restore = _install_fake_freecad_console(tmp_path)

    def _fails_some_other_way(func, timeout=None):
        raise ImportError("No module named 'PySide2'")

    saved_dispatch = core.apply.on_main_thread
    core.apply.on_main_thread = _fails_some_other_way
    try:
        import utils.logger as ul
        ul.logger.error("boom")
    finally:
        core.apply.on_main_thread = saved_dispatch
        restore()

    assert calls == []


def test_a_signal_of_an_object_that_lives_on_a_worker_thread_needs_a_bound_slot(qt_app):
    """
    Why `LoopWorker.finished` carries its transcript instead of a lambda
    closing over one.

    Qt takes the *sender* as the context of a connection made to a plain
    callable, so an `AutoConnection` from an object that was moved onto a
    worker thread compares that thread against itself and resolves to a
    direct call: the slot runs on the worker thread. Naming a
    `QueuedConnection` does not move it, because the post still goes to the
    context's thread. Only a bound method of a QObject that lives on the GUI
    thread gives Qt a context there.

    Measured against this FreeCAD's own Qt: the lambda and the queued lambda
    both ran on the worker thread, the bound method on the main one. In the
    panel that was `on_run_finished` building widgets off the GUI thread --
    sixteen `QObject::setParent: ... different thread` warnings, one
    `QObject::killTimer: ... another thread`, then a segfault in
    `queue_widget.py :: _Elided.set_full`.
    """
    app, QtCore = qt_app
    main = QtCore.QThread.currentThread()
    ran_on = {}

    class Sender(QtCore.QObject):
        done = QtCore.Signal(object)
        by_hand = QtCore.Signal(object)

        @QtCore.Slot()
        def go(self):
            self.done.emit(None)
            self.by_hand.emit(None)

    class Receiver(QtCore.QObject):
        @QtCore.Slot(object)
        def took_it(self, _):
            ran_on["bound"] = QtCore.QThread.currentThread()

    thread = QtCore.QThread()
    sender = Sender()
    sender.moveToThread(thread)
    receiver = Receiver()
    sender.done.connect(lambda _: ran_on.__setitem__("lambda",
                                                     QtCore.QThread.currentThread()))
    sender.done.connect(lambda _: ran_on.__setitem__("queued_lambda",
                                                     QtCore.QThread.currentThread()),
                        QtCore.Qt.QueuedConnection)
    sender.by_hand.connect(receiver.took_it)
    thread.started.connect(sender.go)
    thread.start()

    deadline = QtCore.QElapsedTimer()
    deadline.start()
    while len(ran_on) < 3 and deadline.elapsed() < 10000:
        app.processEvents()
    thread.quit()
    thread.wait(2000)

    assert ran_on["bound"] == main
    assert ran_on["lambda"] != main
    assert ran_on["queued_lambda"] != main
