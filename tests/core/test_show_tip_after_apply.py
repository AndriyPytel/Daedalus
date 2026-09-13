"""
Level 1 — the Body's Tip stays visible, everything else it owns does not.

`apply()` lands a Step and calls `fit_view()`, which only ever moves the
camera — nothing in the pipeline sets `Visibility` on the modelled result
itself. A run cut off mid-Step (a budget ceiling, an interrupted Plan) can
therefore leave the built geometry hidden with nothing to correct it, since
PartDesign hides a feature the moment something else consumes it and never
un-hides the one still current.

Faked here for the same reason `test_fit_view_ignores_scaffolding.py` fakes
its Document: the invariant is a pure filter over `Shape`/`ViewObject`
state, not FreeCAD's own PartDesign consumption behaviour.
"""
from core.freecad_apply import _show_tip


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
    def __init__(self, visibility=False):
        self.Visibility = visibility


class FakeFeature:
    def __init__(self, diagonal=40.0, faces=True, visibility=False):
        self.Shape = FakeShape(diagonal, faces=faces)
        self.ViewObject = FakeViewObject(visibility)


class FakeBody:
    def __init__(self, group, tip):
        self.TypeId = "PartDesign::Body"
        self.Group = group
        self.Tip = tip


class FakeDocument:
    def __init__(self, objects):
        self.Objects = objects


def test_the_tip_is_shown_and_every_other_feature_in_the_body_is_hidden():
    pad = FakeFeature(visibility=False)
    pocket = FakeFeature(visibility=True)
    body = FakeBody(group=[pad, pocket], tip=pocket)
    document = FakeDocument([body])

    _show_tip(document)

    assert pad.ViewObject.Visibility is False
    assert pocket.ViewObject.Visibility is True


def test_a_mid_run_cut_leaves_the_last_landed_tip_visible():
    sketch = FakeFeature(faces=False, visibility=False)
    pad = FakeFeature(visibility=False)
    body = FakeBody(group=[sketch, pad], tip=pad)
    document = FakeDocument([body])

    _show_tip(document)

    assert pad.ViewObject.Visibility is True


def test_origin_scaffolding_in_the_body_is_left_untouched():
    origin_plane = FakeFeature(diagonal=1e100, visibility=True)
    pad = FakeFeature(visibility=False)
    body = FakeBody(group=[origin_plane, pad], tip=pad)
    document = FakeDocument([body])

    _show_tip(document)

    assert origin_plane.ViewObject.Visibility is True


def test_a_document_with_no_body_is_left_alone():
    document = FakeDocument([FakeFeature(visibility=False)])

    _show_tip(document)
