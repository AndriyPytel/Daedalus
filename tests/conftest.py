import os
import sys
from dataclasses import dataclass

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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

# Importing FreeCAD prepends its Mod directory, where this addon is symlinked
# from one checkout — so a run from a worktree would import that one instead.
if sys.path[0] != ROOT:
    sys.path.remove(ROOT)
    sys.path.insert(0, ROOT)


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


def pytest_collection_modifyitems(config, items):
    if HAVE_FREECAD:
        return
    skip = pytest.mark.skip(reason="FreeCAD not importable; set FREECAD_LIB to its lib dir")
    for item in items:
        if "freecad" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="module")
def modelmania_sheets(tmp_path_factory):
    """The four distinct drawings, parsed once for one test module."""
    from core import sheet_reader
    from tests import modelmania_harness

    if not sheet_reader.available():
        pytest.skip("PyMuPDF is not installed")
    return modelmania_harness.read_sheets(tmp_path_factory)


@pytest.fixture(autouse=True)
def readings_go_to_a_temporary_directory(tmp_path, monkeypatch):
    """
    Where a run writes the part it read, for the length of one test.

    Every run with an attachment writes one, and the default is the directory a
    user opens to find their own readings.
    """
    from core import part_file

    monkeypatch.setattr(part_file, "WHERE", str(tmp_path / "parts"))


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
