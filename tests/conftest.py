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


def _one_qt_application():
    """
    Build the suite's single QApplication, offscreen, before any test does.

    A process gets one QCoreApplication and cannot upgrade it: whichever test
    ran first decided whether widgets could exist at all, and a QDockWidget
    built under a plain QCoreApplication aborts the interpreter rather than
    raising. Making it a GUI application here settles that for everyone —
    QApplication *is* a QCoreApplication, so the event-loop tests are unaffected.
    """
    try:
        from PySide2 import QtWidgets
    except ImportError:
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
