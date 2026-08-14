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
