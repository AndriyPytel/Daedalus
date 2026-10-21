"""
Builds the pool of ABC parts that CADGenBench-like drawing sheets could be made from.
`stats` writes a per-part table of unit, solid count and face counts by surface type; `select`
filters it geometrically, validates the survivors in a seeded shuffle and previews the first
candidates. Run with `harness/.venv/bin/python harness/abc_pool.py`: the OCP work forks workers
and `analyze_step` spawns more, so everything stays under the `__main__` guard.
"""
import argparse
import csv
import ctypes.util
import json
import os
import random
import re
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

CACHE = Path.home() / ".cache" / "abc"
CHUNKS = ["abc_0000", "abc_0001"]
TYPES = ["plane", "cylinder", "cone", "torus", "sphere", "bspline"]
COLUMNS = ["id", "unit", "solids", "faces", *TYPES, "other"]
UNIT = re.compile(r"SI_UNIT\(\s*(?:\.(MILLI|CENTI)\.|\$)\s*,\s*\.METRE\.\s*\)|CONVERSION_BASED_UNIT\(\s*'(INCH|METRE)'")
UNIT_NAMES = {"MILLI": "mm", "CENTI": "cm", "INCH": "inch", "METRE": "m", None: "m"}
SEED = 0
CANDIDATES = 150
FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
HOMEBREW_CAIRO = "/opt/homebrew/lib/libcairo.2.dylib"
SHEET_VIEWS = Path(__file__).resolve().parent / "sheet_views.py"
OUT = Path("/tmp/myga-orchestrate-2026-10-20-1900/candidates")
CANDIDATE_IDS = CACHE / "abc_candidates.json"
FILTER_STEPS = [
    ("1 solid", lambda r: r["solids"] == 1),
    ("150-1000 faces", lambda r: 150 <= r["faces"] <= 1000),
    (">=1 torus", lambda r: r["torus"] >= 1),
    ("bspline <= 40%", lambda r: 10 * r["bspline"] <= 4 * r["faces"]),
]


def step_unit(path):
    """Length unit named in a STEP file, or "" when none is found."""
    with open(path, "rb") as handle:
        match = UNIT.search(handle.read().decode("latin-1"))
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


def stats_path(chunk):
    return CACHE / f"{chunk}_stats.csv"


def step_paths():
    return {p.stem: p for chunk in CHUNKS for p in (CACHE / chunk).rglob("*.step")}


def write_stats(chunk, workers=8):
    files = sorted((CACHE / chunk).rglob("*.step"))
    out = stats_path(chunk)
    with ProcessPoolExecutor(workers) as pool, open(out, "w", newline="") as handle:
        writer = csv.DictWriter(handle, COLUMNS)
        writer.writeheader()
        rows = [row for row in pool.map(safe_row, files, chunksize=16) if row]
        writer.writerows(rows)
    return len(files), len(rows)


def load_stats():
    rows = []
    for chunk in CHUNKS:
        with open(stats_path(chunk), newline="") as handle:
            rows += [{k: (v if k in ("id", "unit") else int(v)) for k, v in row.items()} for row in csv.DictReader(handle)]
    return rows


def passes_filter(row):
    return all(test(row) for _, test in FILTER_STEPS)


def funnel(rows):
    steps = [("parts", len(rows))]
    for name, test in FILTER_STEPS:
        rows = [r for r in rows if test(r)]
        steps.append((name, len(rows)))
    return steps


def pick_candidates(rows, count=CANDIDATES, seed=SEED):
    """First `count` valid survivors of the seeded shuffle, and how many survivors were examined."""
    from cadgenbench.common.validity import analyze_step

    survivors = sorted(r["id"] for r in rows if passes_filter(r))
    random.Random(seed).shuffle(survivors)
    paths = step_paths()
    chosen, examined = [], 0
    for part in survivors:
        if len(chosen) == count:
            break
        examined += 1
        if analyze_step(paths[part]).validation.is_valid:
            chosen.append(part)
    return chosen, examined


def iso_view(step_path, work):
    """TechDraw isometric visible and hidden edge SVG of the part, scaled to mm first."""
    from OCP.Interface import Interface_Static
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    mm_step = work / "part.step"
    writer = STEPControl_Writer()
    writer.Transfer(read_shape(step_path), STEPControl_AsIs)
    writer.Write(str(mm_step))
    views = work / "views.json"
    env = {**os.environ, "DAE_STEP": str(mm_step), "DAE_VIEWS": str(views)}
    subprocess.run(
        [FREECADCMD, "-c", f"exec(open({str(SHEET_VIEWS)!r}).read())"],
        env=env, check=True, capture_output=True, timeout=300,
    )
    return json.loads(views.read_text())["FrontTopLeft"]


def preview_png(step_path, png_path, size=600):
    with tempfile.TemporaryDirectory() as tmp:
        view = iso_view(step_path, Path(tmp))
    x0, y0, x1, y1 = view["bbox2d"]
    pad = 0.05 * max(x1 - x0, y1 - y0)
    box = f"{-(x1 - x0) / 2 - pad} {-(y1 - y0) / 2 - pad} {x1 - x0 + 2 * pad} {y1 - y0 + 2 * pad}"
    stroke = max(x1 - x0, y1 - y0) / 400
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" width="{size}" height="{size}">'
        f'<g fill="none" stroke="#000" stroke-width="{stroke:g}" stroke-linecap="round">{view["visible_svg"]}</g></svg>'
    )
    find = ctypes.util.find_library
    ctypes.util.find_library = lambda name: HOMEBREW_CAIRO if name == "cairo" else find(name)
    import cairosvg

    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(png_path), output_width=size, output_height=size, background_color="white")


def contact_sheets(captions, out=OUT, columns=5, rows=6, cell=300):
    from PIL import Image, ImageDraw

    per_sheet = columns * rows
    paths = []
    for index in range(0, len(captions), per_sheet):
        sheet = Image.new("RGB", (columns * cell, rows * (cell + 24)), "white")
        draw = ImageDraw.Draw(sheet)
        for slot, (part, faces) in enumerate(captions[index:index + per_sheet]):
            x, y = (slot % columns) * cell, (slot // columns) * (cell + 24)
            sheet.paste(Image.open(out / f"{part}.png").convert("RGB").resize((cell, cell)), (x, y))
            draw.text((x + 4, y + cell + 6), f"{part}  {faces} faces", fill="black")
        paths.append(out / f"contact_{index // per_sheet + 1:02d}.png")
        sheet.save(paths[-1])
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("command", choices=["stats", "select", "previews"])
    parser.add_argument("chunk", nargs="?", choices=CHUNKS)
    args = parser.parse_args()
    if args.command == "stats":
        print(write_stats(args.chunk))
    elif args.command == "select":
        rows = load_stats()
        for step in funnel(rows):
            print(step)
        chosen, examined = pick_candidates(rows)
        CANDIDATE_IDS.write_text(json.dumps({"seed": SEED, "examined": examined, "ids": chosen}, indent=1))
        print(f"{len(chosen)} valid candidates after examining {examined} survivors")
    else:
        paths = step_paths()
        faces = {r["id"]: r["faces"] for r in load_stats()}
        OUT.mkdir(parents=True, exist_ok=True)
        ids = json.loads(CANDIDATE_IDS.read_text())["ids"]
        for part in ids:
            if not (OUT / f"{part}.png").exists():
                preview_png(paths[part], OUT / f"{part}.png")
        print([str(p) for p in contact_sheets([(part, faces[part]) for part in ids])])


if __name__ == "__main__":
    main()
