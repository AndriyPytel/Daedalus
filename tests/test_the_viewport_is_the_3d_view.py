"""
Level 1 — which view the camera work moves when the Document has more than one.

`ActiveDocument.ActiveView` is whichever tab is in front, and a Spreadsheet or
a TechDraw page is a tab of the same Document. Neither has a camera: reading
one as the viewport raised AttributeError out of a Step that had already
landed, and out of the Review capture at the end of a run.

The recovery is the one FreeCAD's own Show module makes — ask the view for a
camera, and where there is none take the Document's 3D view instead. Faked
here because the failure needs two views open at once, which no headless
FreeCAD can have.
"""
import sys
import types

import pytest

from core.freecad_tools import REVIEW_VIEWS, capture_review_views, fit_view


class SpreadsheetView:
    """A tab with no camera: what a Spreadsheet or a TechDraw page offers."""


class View3D:
    """Enough of View3DInventorPy for the two things that move the camera."""

    def __init__(self):
        self.camera = "wherever the user left it"
        self.fits = 0
        self.turned = []
        self.saved = []

    def getCamera(self):
        return self.camera

    def setCamera(self, camera):
        self.camera = camera

    def fitAll(self):
        self.fits += 1

    def saveImage(self, path, width, height, background):
        self.saved.append(path)

    def __getattr__(self, name):
        if name not in REVIEW_VIEWS:
            raise AttributeError(name)
        return lambda: self.turned.append(name)


class GuiDocument:
    def __init__(self, in_front, views):
        self.ActiveView = in_front
        self._views = list(views)

    def mdiViewsOfType(self, type_name):
        return list(self._views) if type_name == "Gui::View3DInventor" else []


@pytest.fixture
def gui(monkeypatch):
    def open_tabs(in_front, views):
        module = types.ModuleType("FreeCADGui")
        module.ActiveDocument = GuiDocument(in_front, views)
        monkeypatch.setitem(sys.modules, "FreeCADGui", module)

    return open_tabs


def test_a_spreadsheet_in_front_does_not_become_the_viewport(gui):
    viewport = View3D()
    gui(SpreadsheetView(), [viewport])

    fit_view()

    assert viewport.fits == 1


def test_the_3d_view_in_front_is_the_one_that_moves(gui):
    in_front, another = View3D(), View3D()
    gui(in_front, [another, in_front])

    fit_view()

    assert (in_front.fits, another.fits) == (1, 0)


def test_a_document_showing_no_3d_view_at_all_is_not_a_failure(gui):
    gui(SpreadsheetView(), [])

    assert fit_view() is None


def test_review_captures_from_the_3d_view_with_a_spreadsheet_in_front(gui, tmp_path):
    viewport = View3D()
    gui(SpreadsheetView(), [viewport])
    paths = [str(tmp_path / (name + ".png")) for name in REVIEW_VIEWS]

    written = capture_review_views(paths)

    assert written == paths
    assert viewport.turned == list(REVIEW_VIEWS)
    assert viewport.camera == "wherever the user left it"


def test_review_with_no_3d_view_leaves_the_rasteriser_to_it(gui, tmp_path):
    gui(SpreadsheetView(), [])

    assert capture_review_views([str(tmp_path / "iso.png")]) == []
