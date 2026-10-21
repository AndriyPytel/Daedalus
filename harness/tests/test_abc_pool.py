import pytest

import abc_pool


def _write(shape, path, unit):
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    Interface_Static.SetCVal_s("write.step.unit", unit)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))


def _box_with_hole():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

    box = BRepPrimAPI_MakeBox(20.0, 20.0, 10.0).Shape()
    hole = BRepPrimAPI_MakeCylinder(3.0, 10.0).Shape()
    return BRepAlgoAPI_Cut(box, hole).Shape()


@pytest.mark.parametrize("unit, name", [("MM", "mm"), ("CM", "cm"), ("M", "m"), ("INCH", "inch")])
def test_step_unit_is_read_from_the_file(tmp_path, unit, name):
    path = tmp_path / "part.step"
    _write(_box_with_hole(), path, unit)
    assert abc_pool.step_unit(path) == name


def test_face_counts_by_type(tmp_path):
    path = tmp_path / "part.step"
    _write(_box_with_hole(), path, "MM")
    stats = abc_pool.stats_row(path)
    assert stats["solids"] == 1
    assert stats["faces"] == 7
    assert stats["plane"] == 6
    assert stats["cylinder"] == 1
    assert stats["torus"] == 0
    assert stats["unit"] == "mm"
    assert stats["id"] == "part"


def _row(**kw):
    row = {"id": "x", "unit": "mm", "solids": 1, "faces": 300, "torus": 2, "bspline": 10}
    return {**row, **kw}


@pytest.mark.parametrize("change, kept", [
    ({}, True), ({"solids": 2}, False), ({"faces": 149}, False), ({"faces": 1001}, False),
    ({"faces": 150}, True), ({"torus": 0}, False), ({"bspline": 121}, False), ({"bspline": 120}, True),
])
def test_geometric_filter(change, kept):
    assert abc_pool.passes_filter(_row(**change)) is kept


def test_funnel_counts_each_step_in_order():
    rows = [_row(), _row(solids=2), _row(faces=50), _row(torus=0), _row(bspline=200)]
    assert abc_pool.funnel(rows) == [("parts", 5), ("1 solid", 4), ("150-1000 faces", 3), (">=1 torus", 2), ("bspline <= 40%", 1)]


@pytest.mark.parametrize("entity, name", [
    ("#35 = ( CONVERSION_BASED_UNIT( 'METRE', #77 )LENGTH_UNIT(  )NAMED_UNIT( #80 ) );", "m"),
    ("#35 = ( LENGTH_UNIT( )NAMED_UNIT( * )SI_UNIT( .MILLI., .METRE. ) );", "mm"),
])
def test_step_unit_of_foreign_exporters(tmp_path, entity, name):
    path = tmp_path / "part.step"
    path.write_text(f"ISO-10303-21;\nDATA;\n{entity}\nENDSEC;\n")
    assert abc_pool.step_unit(path) == name
