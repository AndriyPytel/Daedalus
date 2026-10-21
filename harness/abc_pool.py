"""
Builds the pool of ABC parts that CADGenBench-like drawing sheets could be made from.
`stats` writes a per-part table of unit, solid count and face counts by surface type; `select`
filters it geometrically, validates the survivors in a seeded shuffle and previews the first
candidates. Run with `harness/.venv/bin/python harness/abc_pool.py`: the OCP work forks workers
and `analyze_step` spawns more, so everything stays under the `__main__` guard.
"""
import argparse
import csv
import random
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

CACHE = Path.home() / ".cache" / "abc"
CHUNK = CACHE / "abc_0000"
STATS = CACHE / "abc_0000_stats.csv"
TYPES = ["plane", "cylinder", "cone", "torus", "sphere", "bspline"]
COLUMNS = ["id", "unit", "solids", "faces", *TYPES, "other"]
UNIT = re.compile(r"SI_UNIT\((?:\.(MILLI|CENTI)\.|\$),\.METRE\.\)|CONVERSION_BASED_UNIT\(\'(INCH)\'")
UNIT_NAMES = {"MILLI": "mm", "CENTI": "cm", "INCH": "inch", None: "m"}
HEADER_BYTES = 1 << 16


def step_unit(path):
    """Length unit named in the first 64 KiB of a STEP file, or "" when none is found."""
    with open(path, "rb") as handle:
        match = UNIT.search(handle.read(HEADER_BYTES).decode("latin-1"))
    if not match:
        return ""
    return UNIT_NAMES[match.group(1) or match.group(2)]


def _surface_types():
    from OCP.GeomAbs import GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus

    return {
        GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
        GeomAbs_Torus: "torus", GeomAbs_Sphere: "sphere", GeomAbs_BSplineSurface: "bspline",
    }


def read_shape(path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"cannot read {path}")
    reader.TransferRoots()
    return reader.OneShape()


def stats_row(path):
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    path = Path(path)
    shape = read_shape(path)
    faces, solids = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    TopExp.MapShapes_s(shape, TopAbs_SOLID, solids)
    names = _surface_types()
    row = {"id": path.stem, "unit": step_unit(path), "solids": solids.Extent(), "faces": faces.Extent()}
    row.update({name: 0 for name in [*TYPES, "other"]})
    for i in range(1, faces.Extent() + 1):
        row[names.get(BRepAdaptor_Surface(TopoDS.Face_s(faces.FindKey(i))).GetType(), "other")] += 1
    return row


def safe_row(path):
    try:
        return stats_row(path)
    except Exception:
        return None


def write_stats(chunk=CHUNK, out=STATS, workers=8):
    files = sorted(Path(chunk).rglob("*.step"))
    with ProcessPoolExecutor(workers) as pool, open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS)
        writer.writeheader()
        rows = [row for row in pool.map(safe_row, files, chunksize=16) if row]
        writer.writerows(rows)
    return len(files), len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("command", choices=["stats"])
    args = parser.parse_args()
    if args.command == "stats":
        print(write_stats())


if __name__ == "__main__":
    main()
