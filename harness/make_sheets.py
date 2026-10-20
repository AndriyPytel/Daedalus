"""
Generates the Fusion 360 Gallery drawing sheets in test-data/: a seeded sample
of single-solid designs, each drawn as an A2 first-angle sheet with its STEP
kept as exemplar.step (mm). Build the venv once:

    brew install cairo
    uv pip install --python harness/.venv/bin/python cairosvg pytest

Run it with `harness/.venv/bin/python harness/make_sheets.py`, never with
`.venv/bin/python` (no OCP) or FreeCAD's own Python; the projection runs in
FreeCAD's `freecadcmd` as a subprocess, and `analyze_step` spawns workers, so
everything here stays under the `__main__` guard. The zip is fetched once into
~/.cache/fusion360-gallery and reruns read it offline.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

import sheet_dimensions  # noqa: E402
import sheet_render  # noqa: E402

ZIP_URL = "https://fusion-360-gallery-dataset.s3.us-west-2.amazonaws.com/reconstruction/r1.0.1/r1.0.1.zip"
CACHE = Path.home() / ".cache" / "fusion360-gallery" / "r1.0.1.zip"
FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
TEMPLATE = "/Applications/FreeCAD.app/Contents/Resources/share/Mod/TechDraw/Templates/ISO/A2_Landscape_ISO5457_minimal.svg"
LOCAL_DESIGNS = HARNESS / "data" / "fusion360"
DESIGN_ID = re.compile(r"^\d+_[0-9a-z]+_\d{4}$")
FEATURES = range(3, 16)


def fetch_zip():
    import httpx

    if CACHE.exists():
        return CACHE
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    part = CACHE.with_suffix(".part")
    with httpx.stream("GET", ZIP_URL, follow_redirects=True, timeout=60) as response, part.open("wb") as out:
        response.raise_for_status()
        for chunk in response.iter_bytes(1 << 20):
            out.write(chunk)
    part.rename(CACHE)
    return CACHE


def shuffled_pool(member_names, excluded, seed):
    ids = {Path(name).stem for name in member_names if name.endswith(".json")}
    pool = sorted(i for i in ids if DESIGN_ID.match(i) and i not in excluded)
    random.Random(seed).shuffle(pool)
    return pool


def split(ids, test, seed):
    chosen = set(random.Random(seed).sample(ids, test))
    return [i for i in ids if i in chosen], [i for i in ids if i not in chosen]


def _read_shape(step_path):
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
        raise RuntimeError(f"cannot read {step_path}")
    reader.TransferRoots()
    return reader.OneShape()


def _solid_count(shape):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _write_step(shape, path):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))


def _project(exemplar, work):
    views_path = work / "views.json"
    env = {**os.environ, "DAE_STEP": str(exemplar), "DAE_VIEWS": str(views_path)}
    script = HARNESS / "sheet_views.py"
    subprocess.run(
        [FREECADCMD, "-c", f"exec(open({str(script)!r}).read())"],
        env=env, check=True, capture_output=True, timeout=300,
    )
    return json.loads(views_path.read_text())


def build_sheet(design_dir, out):
    """Write the five files of one design into `out`; return its missing features."""
    design_dir = Path(design_dir)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    source_text = (design_dir / f"{design_dir.name}.json").read_text()
    source = json.loads(source_text)
    (out / "source.json").write_text(source_text)
    from OCP.Interface import Interface_Static

    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    shape = _read_shape(design_dir / f"{design_dir.name}.step")
    _write_step(shape, out / "exemplar.step")
    with tempfile.TemporaryDirectory() as tmp:
        views = _project(out / "exemplar.step", Path(tmp))
    dims = sheet_dimensions.timeline_dimensions(source)
    dims += sheet_dimensions.step_dimensions(shape, dims)
    missing = sheet_dimensions.missing_features(source, dims)
    records = [d.to_dict() for d in dims]
    (out / "dimensions.json").write_text(json.dumps(records, indent=1))
    svg = sheet_render.render_svg(Path(TEMPLATE).read_text(), views, records)
    sheet_render.write_sheet(svg, out / "input.png", out / "sheet.pdf")
    return missing


def acceptable(source, shape):
    entities = source["entities"]
    if len(source["timeline"]) not in FEATURES:
        return False
    sketches = [e for e in entities.values() if e["type"] == "Sketch"]
    return all(sheet_dimensions.sketch_axis(s) is not None for s in sketches) and _solid_count(shape) == 1


def generate(out, seed, count, test):
    from cadgenbench.common.validity import analyze_step

    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    staging = out / "_accepted"
    excluded = {p.name for p in LOCAL_DESIGNS.iterdir()} if LOCAL_DESIGNS.exists() else set()
    accepted, skipped = [], 0
    with zipfile.ZipFile(fetch_zip()) as archive:
        names = {Path(n).stem + Path(n).suffix: n for n in archive.namelist()}
        for design in shuffled_pool(archive.namelist(), excluded, seed):
            if len(accepted) == count:
                break
            if f"{design}.step" not in names:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                work = Path(tmp) / design
                work.mkdir()
                for suffix in ("json", "step"):
                    (work / f"{design}.{suffix}").write_bytes(archive.read(names[f"{design}.{suffix}"]))
                try:
                    source = json.loads((work / f"{design}.json").read_text())
                    ok = acceptable(source, _read_shape(work / f"{design}.step"))
                    ok = ok and not build_sheet(work, staging / design)
                    ok = ok and analyze_step(staging / design / "exemplar.step").validation.is_valid
                except (RuntimeError, subprocess.SubprocessError, KeyError, ValueError):
                    ok = False
            if ok:
                accepted.append(design)
            else:
                skipped += 1
                shutil.rmtree(staging / design, ignore_errors=True)
            print(f"{len(accepted)}/{count} accepted, {skipped} skipped", flush=True)
    test_ids, validation_ids = split(accepted, test, seed)
    for group, ids in (("test", test_ids), ("validation", validation_ids)):
        (out / group).mkdir(parents=True)
        for design in ids:
            shutil.move(str(staging / design), str(out / group / design))
    staging.rmdir()
    (out / "split.json").write_text(
        json.dumps({"seed": seed, "source": ZIP_URL, "test": test_ids, "validation": validation_ids}, indent=1)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default="test-data")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--test", type=int, default=70)
    args = parser.parse_args()
    generate(args.out, args.seed, args.count, args.test)


if __name__ == "__main__":
    main()
