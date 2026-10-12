"""
Level 2 -- every face of the base part has an address: the words Design and Plan name it by, and the key the per-Step collateral check finds it gone by.
"""
import pytest

pytestmark = pytest.mark.freecad


def _spline_block():
    import FreeCAD
    import Part

    def curve(z, bulge):
        points = [FreeCAD.Vector(x, bulge * (1 if x == 5 else 0), z) for x in (0, 5, 10)]
        spline = Part.BSplineCurve()
        spline.interpolate(points)
        return spline.toShape()

    return Part.makeLoft([curve(0, 3), curve(10, 3)], False)


def _addresses(shape):
    from core.freecad_apply import _addresses_of

    return _addresses_of(shape)


def test_a_plane_a_cylinder_a_cone_a_torus_and_a_spline_each_get_one_address():
    import Part

    assert len(_addresses(Part.makeBox(4, 5, 6))) == 6
    assert any(a.startswith("plane z = 6") for a in _addresses(Part.makeBox(4, 5, 6)))
    cylinder = _addresses(Part.makeCylinder(3, 10))
    assert any(a.startswith("boss diameter 6, axis (0, 0, 1)") for a in cylinder)
    cone = [a for a in _addresses(Part.makeCone(10, 5, 20)) if a.startswith("cone")]
    assert len(cone) == 1 and "axis (0, 0, 1)" in cone[0] and "apex (0, 0, 40)" in cone[0] and "angle 14.04" in cone[0]
    torus = _addresses(Part.makeTorus(20, 3))
    assert len(torus) == 1 and "axis (0, 0, 1)" in next(iter(torus)) and "radii 20 and 3" in next(iter(torus))
    spline = [a for a in _addresses(_spline_block()) if "bbox" in a]
    assert spline


def test_an_address_is_the_same_across_two_builds_of_the_part():
    import Part

    assert set(_addresses(Part.makeCone(10, 5, 20))) == set(_addresses(Part.makeCone(10, 5, 20)))
    assert set(_addresses(_spline_block())) == set(_addresses(_spline_block()))


def test_an_address_is_gone_when_its_face_is_removed_and_kept_when_only_its_area_changes():
    import FreeCAD
    import Part

    plate = Part.makeBox(40, 30, 10)
    holed = plate.cut(Part.makeCylinder(3, 10, FreeCAD.Vector(20, 15, 0)))
    wider = Part.makeBox(40, 40, 10)

    before = set(_addresses(plate))
    assert before <= set(_addresses(holed))
    assert any(a.startswith("hole diameter 6") for a in set(_addresses(holed)) - before)
    assert "plane z = 10" in _addresses(wider) and _addresses(wider)["plane z = 10"] != _addresses(plate)["plane z = 10"]
    assert "plane y = 30" not in _addresses(wider)


def test_the_inventory_lists_a_cone_by_its_address_and_not_by_a_count():
    import FreeCAD
    import Part

    from core.freecad_apply import base_inventory

    doc = FreeCAD.newDocument("Addresses")
    feature = doc.addObject("Part::Feature", "Imported")
    feature.Shape = Part.makeCone(10, 5, 20)
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = feature
    doc.recompute()
    try:
        text = base_inventory(doc)
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert "cone axis (0, 0, 1), apex (0, 0, 40), angle 14.04" in text
    assert "x1" not in text


def test_the_tool_reports_the_base_part_s_addresses_and_the_Body_s_now():
    import FreeCAD
    import Part

    from core.freecad_apply import build_registry

    doc = FreeCAD.newDocument("Now")
    feature = doc.addObject("Part::Feature", "Imported")
    feature.Shape = Part.makeBox(40, 30, 10)
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = feature
    doc.recompute()
    try:
        said = build_registry(document=doc).call("base_addresses")
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert said["base"] == said["now"] and "plane z = 10" in said["base"]


def test_a_spline_face_trimmed_by_a_cut_beside_it_keeps_its_address():
    import FreeCAD
    import Part

    block = _spline_block()
    cutter = Part.makeBox(4, 40, 40, FreeCAD.Vector(-2, -20, -20))
    trimmed = block.cut(cutter)
    kept = {a for a in _addresses(block) if "bbox" in a}

    assert kept and kept <= set(_addresses(trimmed))


def test_a_spline_face_that_is_removed_is_gone():
    import FreeCAD
    import Part

    block = _spline_block()
    cutter = Part.makeBox(40, 40, 40, FreeCAD.Vector(-5, -20, -20))
    kept = {a for a in _addresses(block) if "bbox" in a}

    assert kept and not kept & set(_addresses(block.cut(cutter)))
