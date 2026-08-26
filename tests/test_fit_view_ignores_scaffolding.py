"""
Level 1 — the camera fit does not zoom out to an Origin plane's bounding box.

`core/freecad_tools.py :: _overall_size` already refuses anything past
`_SCAFFOLDING` (1e6 mm) because a Body's Origin carries three planes and
three axes, each 1e100 mm across, and a literal union of every bounding box
makes any part 2e+100 mm wide. `fit_view` calls the raw `view.fitAll()`,
which fits the camera to everything currently visible with no filter of its
own — the same failure mode, on the camera path this time, and the part
becomes an unrenderable speck the moment one of those features is visible.

Faked here because the failure needs a `Shape.BoundBox` a kilometre or more
across, which no in-process test can build from a real FreeCAD document
without FreeCAD itself.
"""
import sys
import types

import pytest

from core.freecad_tools import _SCAFFOLDING, fit_view


class FakeBoundBox:
    def __init__(self, diagonal):
        self.DiagonalLength = diagonal

    def isValid(self):
        return True


class FakeShape:
    def __init__(self, diagonal, faces=True):
        self.BoundBox = FakeBoundBox(diagonal)
        self._faces = faces

    def isNull(self):
        return False

    @property
    def Faces(self):
        return [object()] if self._faces else []


class FakeViewObject:
    def __init__(self):
        self.Visibility = True


class FakeObject:
    def __init__(self, diagonal, faces=True):
        self.Shape = FakeShape(diagonal, faces=faces)
        self.ViewObject = FakeViewObject()


class FakeDocument:
    def __init__(self, objects, name="Doc"):
        self.Objects = objects
        self.Name = name


class View3D:
    def __init__(self):
        self.fits = 0

    def getCamera(self):
        return "camera"

    def fitAll(self):
        self.fits += 1


class GuiDocument:
    def __init__(self, view):
        self.ActiveView = view


@pytest.fixture
def gui(monkeypatch):
    def open_view(view, name="Doc"):
        module = types.ModuleType("FreeCADGui")
        gui_document = GuiDocument(view)
        module.ActiveDocument = gui_document
        module.getDocument = lambda document_name: gui_document
        monkeypatch.setitem(sys.modules, "FreeCADGui", module)

    return open_view


def test_an_origin_plane_is_hidden_before_the_fit_and_shown_again_after(gui):
    view = View3D()
    gui(view)
    part = FakeObject(40.0)
    origin_plane = FakeObject(1e100)
    document = FakeDocument([part, origin_plane])

    fit_view(document)

    assert view.fits == 1
    assert part.ViewObject.Visibility is True
    assert origin_plane.ViewObject.Visibility is True


def test_the_origin_plane_stays_hidden_only_for_the_duration_of_the_fit(gui):
    seen_during_fit = {}
    view = View3D()

    class RecordingView(View3D):
        def fitAll(self):
            seen_during_fit["origin_visible"] = origin_plane.ViewObject.Visibility
            super().fitAll()

    view = RecordingView()
    gui(view)
    part = FakeObject(40.0)
    origin_plane = FakeObject(1e100)
    document = FakeDocument([part, origin_plane])

    fit_view(document)

    assert seen_during_fit["origin_visible"] is False


def test_an_already_hidden_scaffolding_object_is_left_hidden(gui):
    view = View3D()
    gui(view)
    part = FakeObject(40.0)
    origin_plane = FakeObject(1e100)
    origin_plane.ViewObject.Visibility = False
    document = FakeDocument([part, origin_plane])

    fit_view(document)

    assert origin_plane.ViewObject.Visibility is False


def test_a_part_at_the_scaffolding_threshold_itself_is_not_hidden(gui):
    view = View3D()
    gui(view)
    part = FakeObject(_SCAFFOLDING)
    document = FakeDocument([part])

    fit_view(document)

    assert part.ViewObject.Visibility is True
