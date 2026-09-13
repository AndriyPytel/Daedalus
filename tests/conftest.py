"""
Test infrastructure: FreeCAD and Qt imports, fixtures, and the suite's QApplication.

Importing FreeCAD prepends its Mod directory, where this addon is symlinked
from one checkout, so a run from a worktree would import that one instead.
The path manipulation after the FreeCAD import restores the checkout's own
modules to the front of sys.path.

`FREECAD_USER_HOME` is set before that import: FreeCAD reads it once, at
startup, to decide where `user.cfg` lives. Redirecting it to a throwaway
directory here means the whole process, not just `SettingsManager`'s own
group, never opens the user's real configuration -- the file a concurrent
`pytest -n auto` run once truncated to nothing, taking the stored API keys
and session list with it.
"""
import atexit
import glob
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass

import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MOD = os.path.join(ROOT, "Mod")
for entry in (SRC, MOD):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from core.loop import execute as loop


def _redirected_freecad_home():
    """
    A throwaway `UserAppData`, with the real `AdditionalPythonPackages`
    symlinked back in.

    That directory is where FreeCAD's own Addon Manager puts `litellm`,
    `httpx` and everything they pull in -- nothing in this checkout
    installs them into the venv, so losing that directory to the redirect
    would fail every import that reaches an LLM provider, not just the
    settings this redirect exists to protect.
    """
    home = tempfile.mkdtemp(prefix="freecadcopilot-test-freecad-home-")
    real_packages = sorted(glob.glob(os.path.expanduser(
        "~/Library/Application Support/FreeCAD/*/AdditionalPythonPackages")))
    if real_packages:
        os.symlink(real_packages[-1], os.path.join(home, "AdditionalPythonPackages"))
    return home


_FREECAD_HOME = _redirected_freecad_home()
atexit.register(shutil.rmtree, _FREECAD_HOME, ignore_errors=True)
os.environ["FREECAD_USER_HOME"] = _FREECAD_HOME


def _freecad_lib_dirs():
    """Candidate directories holding FreeCAD.so, in order of preference."""
    env = os.environ.get("FREECAD_LIB")
    if env:
        yield env
    yield "/Applications/FreeCAD.app/Contents/Resources/lib"
    yield "/usr/lib/freecad/lib"
    yield "/usr/lib/freecad-python3/lib"


def _try_import_freecad():
    try:
        import FreeCAD  # noqa: F401
        return True
    except ImportError:
        pass
    for lib in _freecad_lib_dirs():
        if not os.path.isdir(lib):
            continue
        sys.path.insert(0, lib)
        try:
            import FreeCAD  # noqa: F401
            return True
        except ImportError:
            sys.path.remove(lib)
    return False


HAVE_FREECAD = _try_import_freecad()

for entry in (ROOT, SRC, MOD):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)


def _import_qt():
    """
    The Qt modules, by whichever route this FreeCAD offers them.

    The same two-step every `ui/*.py` module makes: PySide2 where it exists,
    otherwise FreeCAD's own `Ext/PySide` shim, which re-exports PySide6 under
    the Qt5 spelling. Resolving it here once is what lets a test say `QtCore`
    without naming a binding — a test that names one is a test that runs on
    one machine.
    """
    for binding in ("PySide2", "PySide"):
        try:
            pkg = __import__(binding, fromlist=["QtCore", "QtGui", "QtWidgets"])
            return pkg.QtCore, pkg.QtGui, pkg.QtWidgets
        except (ImportError, AttributeError):
            continue
    return None, None, None


QtCore, QtGui, QtWidgets = _import_qt()
HAVE_QT = QtWidgets is not None
NO_QT = "Qt is not available: neither PySide2 nor FreeCAD's PySide shim"


def _one_qt_application():
    """
    Build the suite's single QApplication, offscreen, before any test does.

    A process gets one QCoreApplication and cannot upgrade it: whichever test
    ran first decided whether widgets could exist at all, and a QDockWidget
    built under a plain QCoreApplication aborts the interpreter rather than
    raising. Making it a GUI application here settles that for everyone —
    QApplication *is* a QCoreApplication, so the event-loop tests are unaffected.
    """
    if not HAVE_QT:
        return None
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


QT_APP = _one_qt_application()

_STARTED_QTHREADS = None
if HAVE_QT:
    import weakref

    _STARTED_QTHREADS = weakref.WeakSet()
    _qthread_start = QtCore.QThread.start

    def _tracked_start(self, *args, **kwargs):
        _STARTED_QTHREADS.add(self)
        return _qthread_start(self, *args, **kwargs)

    QtCore.QThread.start = _tracked_start


@pytest.fixture(autouse=True, scope="module")
def no_module_leaves_a_qthread_running():
    """
    A QThread still alive when a module's tests are done is the next module's
    deadlock, not this one's failure. Scoped to the module, not the test: a
    Session's own worker thread is meant to outlive any one test in a file
    like `test_panel_level2.py`, where the `panel` fixture is module-scoped
    and Sessions carry their thread across the whole file by design (FCC-181)
    — checking per test would fail every one of them. What must not survive
    past the module's own teardown (`panel`'s `built._cleanup_thread()`, or
    whatever the module's fixtures do) is a thread nothing is tracking
    anymore, `_TitleThread` among them: the failure `the_suite_does_not_talk_to_the_network`
    already names in its docstring — a queued signal landing in a lock the
    leaked thread still holds, with no traceback, just a frozen run.

    Tracking every `QThread.start()` call and checking once the module's own
    fixtures have torn down is what turns that hang into a named, attributed
    failure instead: the two seconds given here is a grace period for a
    thread already on its way out, not a wait for one still doing work.
    """
    if not HAVE_QT:
        yield
        return
    before = {id(t) for t in _STARTED_QTHREADS}
    yield
    started_here = [t for t in _STARTED_QTHREADS if id(t) not in before]
    for thread in started_here:
        if thread.isRunning():
            thread.wait(2000)
    leaked = [t for t in started_here if t.isRunning()]
    assert not leaked, (
        "this module left %d QThread(s) running past its own teardown: %s "
        "— the next test's processEvents() can dispatch a queued signal "
        "into a lock one of these still holds, deadlocking the whole run"
        % (len(leaked), [type(t).__name__ for t in leaked])
    )


def pytest_collection_modifyitems(config, items):
    if HAVE_FREECAD:
        return
    skip = pytest.mark.skip(reason="FreeCAD not importable; set FREECAD_LIB to its lib dir")
    for item in items:
        if "freecad" in item.keywords:
            item.add_marker(skip)


_SHEET_TEST_FILES = {
    "test_sheet_reader.py",
    "test_modelmania_evidence.py",
    "test_sketch_check_level2.py",
}


def pytest_xdist_make_scheduler(config, log):
    """
    Puts the three files that share `tests/modelmania/*.pdf` on one worker
    under `--dist loadfile` (FCC-283).

    `@pytest.mark.xdist_group("sheets")` on those files, as the ticket asks
    for, does nothing under `loadfile`: `LoadFileScheduling._split_scope`
    (xdist/scheduler/loadfile.py) only ever looks at the nodeid's file part,
    and the marker only affects scheduling under `--dist loadgroup`, which
    scopes every *other*, unmarked test to itself instead of its file —
    switching the whole suite's `--dist` would cost every other module the
    file-level batching it already relies on. This scheduler keeps
    `loadfile`'s behaviour for every file except these three, which it
    merges into one scope so `sheet_cache`'s session fixture is the same
    dict for all of them.
    """
    if config.getvalue("dist") != "loadfile":
        return None
    from xdist.scheduler import LoadFileScheduling

    class _SheetFilesShareAWorker(LoadFileScheduling):
        def _split_scope(self, nodeid):
            scope = super()._split_scope(nodeid)
            if os.path.basename(scope) in _SHEET_TEST_FILES:
                return "sheets"
            return scope

    return _SheetFilesShareAWorker(config, log)


@pytest.fixture(scope="session")
def sheet_cache(tmp_path_factory):
    """
    Every distinct PDF sheet, parsed once for the whole test session.

    `core.sheet_reader.read()` already caches a parsed sheet, but keys the
    cache on the temp directory it is asked to write crops into as well as the
    path, and each module-scoped fixture that wanted a sheet asked for its own
    `tmp_path_factory.mktemp`. Three modules reading the same `mm25-p1-1.pdf`
    got three renders. This fixture is the one place a sheet is asked for by
    path, so a second module gets back what the first one already parsed.

    That dedup is per-worker: under `-n 8 --dist loadfile` each worker gets
    its own `session`-scoped instance of this fixture, so the same PDF is
    still parsed once per worker unless every file that shares it is pinned
    to the same worker (`pytest_xdist_make_scheduler` above, FCC-283).
    """
    from core.sheets import sheet_reader

    cache = {}

    def read(path):
        if not os.path.exists(path):
            pytest.skip("sheet not found: %s; set FCC_MODELMANIA_DIR" % path)
        key = os.path.abspath(path)
        if key not in cache:
            cache[key] = sheet_reader.read(path, into=str(tmp_path_factory.mktemp("sheet")))
        return cache[key]

    return read


@pytest.fixture(scope="module")
def modelmania_sheets(sheet_cache):
    """The four distinct drawings, read through the session's shared cache."""
    from core.sheets import sheet_reader
    from tests import modelmania_harness

    if not modelmania_harness.MODELMANIA_DIR.is_dir():
        pytest.skip("modelmania sheets not found; set FCC_MODELMANIA_DIR")
    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return modelmania_harness.read_sheets(sheet_cache)


@pytest.fixture(scope="session", autouse=True)
def the_suite_does_not_talk_to_the_network():
    """
    Loopback is the only address a test may connect to, for the life of the run.

    A panel built here runs the real send path, and that path names the Session
    with one short call to whichever provider the developer has configured: a
    real request, on their own key, out of a unit test. The reply came back on
    a QThread that outlived the test, and the next test's `processEvents()`
    dispatched its signal into a lock the thread still held — the whole run
    stopped there. The local-endpoint and model-catalog tests speak to HTTP
    servers they stand up themselves on 127.0.0.1, so loopback stays open.

    A per-test `monkeypatch` guard leaves a gap between tests, while pytest is
    tearing one test's fixtures down and setting the next one's up, where the
    real `socket.connect` is back in place. That gap is a few instructions
    wide and never seen in isolation, but a `_TitleThread` scheduled late
    under CPU contention from other work on the machine can land its connect
    inside exactly that gap and complete a real request. Patching once for
    the whole session, undone only when the session ends, closes it — the
    guard is never off while any test could still be running.
    """
    import socket

    def refuse(address):
        host = address[0] if isinstance(address, tuple) else ""
        if host in ("", "localhost", "127.0.0.1", "::1"):
            return
        raise RuntimeError("the suite does not talk to the network: %r" % (address,))

    mp = pytest.MonkeyPatch()
    for name in ("connect", "connect_ex"):
        reaching_out = getattr(socket.socket, name)

        def guarded(self, address, _reaching_out=reaching_out):
            refuse(address)
            return _reaching_out(self, address)

        mp.setattr(socket.socket, name, guarded)
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def readings_go_to_a_temporary_directory(tmp_path, monkeypatch):
    """
    Where a run writes the part it read, for the length of one test.

    Every run with an attachment writes one, and the default is the directory a
    user opens to find their own readings.
    """
    from core.design import part_file

    monkeypatch.setattr(part_file, "WHERE", str(tmp_path / "parts"))


@pytest.fixture(autouse=True)
def the_type_cache_goes_to_a_temporary_directory(tmp_path, monkeypatch):
    """
    Where describe_type cache is written, for the length of one test.

    The default is repo/logs/type_cache.json, which under pytest gives the
    checkout; under FreeCAD it resolves through the symlink in Mod/ and writes
    to the user's data directory. Redirecting it to tmp_path means tests run
    isolated.
    """
    import core.loop.execute as loop_execute_module

    monkeypatch.setattr(loop_execute_module, "TYPE_CACHE_PATH", str(tmp_path / "type_cache.json"))


@dataclass
class Reply:
    """A whole streamed reply, accumulated. Only the tests ever need one."""
    text: str = ""
    reasoning: str = ""


def collect(chunks) -> Reply:
    """Drain a Chunk stream into the reply it spells out."""
    from core.llm import REASONING

    reply = Reply()
    for chunk in chunks:
        if chunk.kind == REASONING:
            reply.reasoning += chunk.text
        else:
            reply.text += chunk.text
    return reply


class ScratchParameters:
    """
    The parameter group `SettingsManager` owns, held in memory for one session.

    `FreeCAD.ParamGet` is the user's own `user.cfg`, and the suite both reads
    and writes it: the read made 44 Plan Gate tests answer to whatever the
    developer last set in the General page, and the write, from several xdist
    workers at once, truncated that file to nothing on 2026-09-02, taking the
    stored API keys and session list with it.

    Only the four accessors `SettingsManager` calls are implemented, so a
    fifth one added there fails loudly here rather than silently reaching the
    real file.
    """

    def __init__(self):
        self._values = {}

    def GetString(self, name, default=""):
        return self._values.get(name, default)

    def SetString(self, name, value):
        self._values[name] = str(value)

    def GetBool(self, name, default=False):
        return bool(self._values.get(name, default))

    def SetBool(self, name, value):
        self._values[name] = bool(value)


@pytest.fixture(scope="session", autouse=True)
def the_settings_never_reach_the_user_s_freecad():
    """
    Where `SettingsManager` stores everything, for the length of the session.

    The scope is the session because `test_vertical_slice_level2.py` drives
    its run from a module-scoped fixture, which is built before any
    function-scoped patch could take effect.
    """
    from core import settings

    scratch = ScratchParameters()
    real = settings.Settings._grp
    settings.Settings._grp = staticmethod(lambda: scratch)
    yield scratch
    settings.Settings._grp = real


@pytest.fixture
def clean_auto_approve(the_settings_never_reach_the_user_s_freecad):
    """
    The manager with `AutoApprovePlan` off, for a test that turns it on.

    The store it writes to lives for the whole session, so the flag is put
    back on the way out as well: a test that leaves it on makes every later
    test in the same process take the auto-approved branch of the Gate.
    """
    from bridge import settings_manager

    the_settings_never_reach_the_user_s_freecad.SetBool("AutoApprovePlan", False)
    yield settings_manager.instance()
    the_settings_never_reach_the_user_s_freecad.SetBool("AutoApprovePlan", False)
