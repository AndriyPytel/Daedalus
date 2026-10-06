"""
Level 2 -- the mesh gate CADGenBench runs on every STEP, and the repair `export_step` applies
only when that gate fails.

OCC's `isValid()` is True for a cylinder face exactly tangent to another face: the wall there has
zero thickness and the official mesher welds it into 4 triangles on one edge. Each shape below is
the smallest solid with that contact.
"""
import warnings

import pytest

pytestmark = pytest.mark.freecad

Part = pytest.importorskip("FreeCAD") and pytest.importorskip("Part")
V = pytest.importorskip("FreeCAD").Vector


def _pocket_tangent_to_a_wall():
    plate = Part.makeBox(100, 60, 4)
    notch = Part.makeCylinder(8, 4, V(50, 52, 0))
    return plate.cut(notch)


def _arc_tangent_to_a_block():
    disc = Part.makeCylinder(20, 10)
    block = Part.makeBox(20, 35, 10, V(20, -10, 0))
    bridge = Part.makeBox(40, 13, 10, V(0, 12, 0))
    return disc.fuse(block).fuse(bridge).removeSplitter()


def _hole_tangent_to_both_sides():
    plate = Part.makeBox(1.2, 1.0, 1.0)
    return plate.cut(Part.makeCylinder(0.5, 1.0, V(0.6, 0.5, 0)))


def _box_with_a_hole():
    return Part.makeBox(40, 30, 10).cut(Part.makeCylinder(5, 10, V(20, 15, 0)))


@pytest.mark.parametrize("build", [_pocket_tangent_to_a_wall, _arc_tangent_to_a_block,
                                   _hole_tangent_to_both_sides])
def test_a_tangent_contact_fails_the_gate(build):
    from core.manifold import check_fc

    assert check_fc(build()) is not None


def test_a_pocket_tangent_to_a_wall_is_valid_to_occ_all_the_same():
    assert _pocket_tangent_to_a_wall().isValid()


def test_the_failure_names_the_cylinder_and_the_plane_it_is_tangent_to():
    from core.manifold import check_fc

    message = check_fc(_pocket_tangent_to_a_wall())

    assert "cylinder R8" in message
    assert "is tangent to face" in message
    assert "(plane)" in message


def test_a_box_with_a_hole_passes_and_is_not_touched():
    from core.manifold import check_fc, repair_fc

    shape = _box_with_a_hole()

    assert check_fc(shape) is None
    fixed, note = repair_fc(shape)
    assert fixed is shape
    assert note is None


@pytest.mark.parametrize("build", [_pocket_tangent_to_a_wall, _arc_tangent_to_a_block])
def test_the_repair_passes_the_gate_and_moves_the_volume_by_under_half_a_percent(build):
    from core.manifold import check_fc, repair_fc

    shape = build()
    fixed, note = repair_fc(shape)

    assert fixed is not shape, note
    assert check_fc(fixed) is None
    assert abs(fixed.Volume - shape.Volume) / shape.Volume < 0.005


def test_a_concave_contact_is_cut_and_a_convex_one_is_fused():
    from core.manifold import repair_fc

    notched = _pocket_tangent_to_a_wall()
    fixed, _ = repair_fc(notched)
    assert fixed.Volume < notched.Volume

    arced = _arc_tangent_to_a_block()
    fixed, _ = repair_fc(arced)
    assert fixed.Volume > arced.Volume


def test_a_repair_that_moves_the_volume_by_half_a_percent_or_more_is_not_applied():
    from core.manifold import repair_fc

    shape = _hole_tangent_to_both_sides()
    fixed, note = repair_fc(shape)

    assert fixed is shape
    assert "volume" in note and "%" in note


def test_export_step_writes_the_repaired_geometry_and_the_gate_passes_on_the_file(tmp_path):
    import FreeCAD

    from core.freecad_apply import export_step
    from core.manifold import check

    doc = FreeCAD.newDocument("manifold_export")
    try:
        doc.addObject("Part::Feature", "Plate").Shape = _pocket_tangent_to_a_wall()
        doc.recompute()
        path = export_step(doc, str(tmp_path / "plate.step"))
        reread = Part.read(path)
    finally:
        FreeCAD.closeDocument(doc.Name)

    from core.manifold import from_freecad

    assert check(from_freecad(reread)) is None


def test_export_step_leaves_a_manifold_model_exactly_as_it_is(tmp_path):
    import FreeCAD

    from core.freecad_apply import export_step

    doc = FreeCAD.newDocument("manifold_untouched")
    try:
        shape = _box_with_a_hole()
        doc.addObject("Part::Feature", "Plate").Shape = shape
        doc.recompute()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            path = export_step(doc, str(tmp_path / "plate.step"))
        reread = Part.read(path)
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert reread.Volume == pytest.approx(shape.Volume, rel=1e-9)


def test_export_step_says_so_when_it_refuses_a_repair(tmp_path):
    import FreeCAD

    from core.freecad_apply import export_step

    doc = FreeCAD.newDocument("manifold_refused")
    try:
        doc.addObject("Part::Feature", "Plate").Shape = _hole_tangent_to_both_sides()
        doc.recompute()
        with pytest.warns(UserWarning, match="volume"):
            path = export_step(doc, str(tmp_path / "plate.step"))
        reread = Part.read(path)
    finally:
        FreeCAD.closeDocument(doc.Name)

    assert reread.Volume == pytest.approx(_hole_tangent_to_both_sides().Volume, rel=1e-9)


def _record_writes(monkeypatch, clean_modes):
    from core import manifold

    written = []
    write = manifold._write

    def recording(shape, path, mode):
        written.append(mode)
        write(shape, path, mode)

    monkeypatch.setattr(manifold, "_write", recording)
    monkeypatch.setattr(manifold, "_reads_back", lambda path: bool(written) and written[-1] in clean_modes)
    return written


def _export_box(tmp_path, name):
    import FreeCAD

    from core.freecad_apply import export_step

    doc = FreeCAD.newDocument(name)
    try:
        doc.addObject("Part::Feature", "Plate").Shape = _box_with_a_hole()
        doc.recompute()
        path = export_step(doc, str(tmp_path / "plate.step"))
        return Part.read(path)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_step_that_does_not_read_back_clean_is_rewritten_in_the_first_precision_mode_that_does(
        tmp_path, monkeypatch):
    written = _record_writes(monkeypatch, clean_modes=(-1,))

    with pytest.warns(UserWarning, match="precision mode -1"):
        reread = _export_box(tmp_path, "precision_ladder")

    assert written == [1, -1]
    assert reread.Volume == pytest.approx(_box_with_a_hole().Volume, rel=1e-6)


def test_a_step_no_precision_mode_reads_back_clean_goes_out_as_first_written(tmp_path, monkeypatch):
    written = _record_writes(monkeypatch, clean_modes=())

    with pytest.warns(UserWarning, match="no STEP precision mode"):
        reread = _export_box(tmp_path, "precision_none")

    assert written == [1, -1]
    assert reread.Volume == pytest.approx(_box_with_a_hole().Volume, rel=1e-9)
