import sys
from pathlib import Path

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def box(x, y, z):
    return BRepPrimAPI_MakeBox(x, y, z).Shape()


def drill(shape, x, y, radius, depth=None, z=0.0, height=100.0):
    cylinder = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), radius, height).Shape()
    return BRepAlgoAPI_Cut(shape, cylinder).Shape()


def wedge_cut(shape, angle_deg):
    import math

    tool = box(200, 200, 200)
    move = gp_Trsf()
    move.SetTranslation(gp_Pnt(0, 0, 0), gp_Pnt(-100, -100, 0))
    tool = BRepBuilderAPI_Transform(tool, move).Shape()
    turn = gp_Trsf()
    from OCP.gp import gp_Ax1

    turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 20), gp_Dir(0, 1, 0)), math.radians(angle_deg))
    tool = BRepBuilderAPI_Transform(tool, turn).Shape()
    lift = gp_Trsf()
    lift.SetTranslation(gp_Pnt(0, 0, 0), gp_Pnt(100, 0, 20))
    tool = BRepBuilderAPI_Transform(tool, lift).Shape()
    return BRepAlgoAPI_Cut(shape, tool).Shape()


@pytest.fixture
def plate():
    part = box(100, 60, 20)
    part = drill(part, 30, 30, 5)
    part = drill(part, 70, 15, 3)
    return drill(part, 70, 45, 3)


def countersink(shape, x, y, top=20.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone

    cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(x, y, top - 5), gp_Dir(0, 0, 1)), 3, 8, 5).Shape()
    return BRepAlgoAPI_Cut(shape, cone).Shape()


def fillet_circles(shape, radius):
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    fillet = BRepFilletAPI_MakeFillet(shape)
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        if BRepAdaptor_Curve(edge).GetType() == GeomAbs_Circle:
            fillet.Add(radius, edge)
        explorer.Next()
    return fillet.Shape()
