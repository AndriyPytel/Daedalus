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
