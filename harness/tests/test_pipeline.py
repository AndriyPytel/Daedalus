import json
import os
from pathlib import Path

import pytest

import make_sheets

FREECADCMD = Path("/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")


@pytest.mark.skipif(not FREECADCMD.exists(), reason="freecadcmd is not installed")
def test_one_design_end_to_end(local_design, tmp_path):
    out = tmp_path / "design"
    missing = make_sheets.build_sheet(local_design, out)
    assert missing == []
    assert sorted(p.name for p in out.iterdir()) == [
        "dimensions.json", "exemplar.step", "input.png", "sheet.pdf", "source.json",
    ]
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Interface import Interface_Static
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    def extent(path, unit):
        Interface_Static.SetCVal_s("xstep.cascade.unit", unit)
        reader = STEPControl_Reader()
        assert reader.ReadFile(str(path)) == IFSelect_RetDone
        reader.TransferRoots()
        box = Bnd_Box()
        BRepBndLib.Add_s(reader.OneShape(), box)
        lo_x, lo_y, lo_z, hi_x, hi_y, hi_z = box.Get()
        return hi_x - lo_x, hi_y - lo_y, hi_z - lo_z

    from cadgenbench.common.validity import analyze_step

    assert analyze_step(out / "exemplar.step").validation.is_valid
    source = extent(local_design / f"{local_design.name}.step", "CM")
    exemplar = extent(out / "exemplar.step", "MM")
    for s, e in zip(source, exemplar):
        assert e == pytest.approx(10 * s, rel=1e-3)
