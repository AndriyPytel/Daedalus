"""
Generates the ABC drawing sheets in test-data/: one A2 first-angle sheet per approved candidate in
~/.cache/abc/abc_candidates.json, each STEP kept as exemplar.step (mm), split 70:30 test:validation
by a seeded sample. Run it with `harness/.venv/bin/python harness/make_sheets.py`, never with
FreeCAD's own Python: the projection runs in `freecadcmd` as a subprocess, `analyze_step` spawns
workers, and everything stays under the `__main__` guard. A candidate that fails coverage,
projection or validity is dropped and logged with its reason in split.json.
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

import abc_pool  # noqa: E402
import sheet_dimensions  # noqa: E402
import sheet_render  # noqa: E402

FREECADCMD = abc_pool.FREECADCMD
TEMPLATE = "/Applications/FreeCAD.app/Contents/Resources/share/Mod/TechDraw/Templates/ISO/A2_Landscape_ISO5457_minimal.svg"
UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4}
TEST_SHARE = 0.7
WORKERS = 6
PROJECTION_TIMEOUT = 600


class Skip(Exception):
    pass


def split(ids, test, seed):
    chosen = set(random.Random(seed).sample(sorted(ids), test))
    return [i for i in ids if i in chosen], [i for i in ids if i not in chosen]


def test_count(total):
    return math.ceil(TEST_SHARE * total)


def _write_step(shape, path):
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))


def _project(exemplar, work):
    views_path = work / "views.json"
    env = {**os.environ, "DAE_STEP": str(exemplar), "DAE_VIEWS": str(views_path)}
    script = HARNESS / "sheet_views.py"
    try:
        subprocess.run(
            [FREECADCMD, "-c", f"exec(open({str(script)!r}).read())"],
            env=env, check=True, capture_output=True, timeout=PROJECTION_TIMEOUT,
        )
        views = json.loads(views_path.read_text())
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        raise Skip("projection: freecadcmd failed") from error
    if any(not views[n]["visible_svg"] for n in ("Front", "Top", "Left", "FrontTopLeft")):
        raise Skip("projection: empty view")
    for name in ("Right", "Bottom", "Rear"):
        if not views[name]["visible_svg"]:
            del views[name]
    return views


def skip_reason(missing):
    return "coverage: " + " ".join(missing[0].split()[:2])


def build_sheet(step_path, out, chunk=""):
    """Write the five files of one part into `out`; return its report. Raises Skip with the reason."""
    step_path, out = Path(step_path), Path(out)
    unit = abc_pool.step_unit(step_path)
    if not unit:
        raise Skip("unit: not readable")
    try:
        shape = sheet_dimensions.orient(abc_pool.read_shape(step_path))
        dims = sheet_dimensions.dimensions(shape)
        missing = sheet_dimensions.missing(shape, dims)
    except RuntimeError as error:
        raise Skip("unreadable: step") from error
    if missing:
        raise Skip(skip_reason(missing))
    out.mkdir(parents=True, exist_ok=True)
    _write_step(shape, out / "exemplar.step")
    with tempfile.TemporaryDirectory() as tmp:
        views = _project(out / "exemplar.step", Path(tmp))
    records = [d.to_dict() for d in dims]
    svg, items, records, placed = sheet_render.render_svg(Path(TEMPLATE).read_text(), views, records)
    sheet_render.write_sheet(svg, out / "input.png", out / "sheet.pdf")
    weights = sheet_render.line_weights(svg)
    counts = sheet_dimensions.face_counts(shape)
    source = {
        "abc_id": step_path.stem, "chunk": chunk, "unit": unit, "unit_factor_to_mm": UNIT_TO_MM[unit],
        "faces": counts, "bspline_share": counts["bspline"] / sum(counts.values()),
        **sheet_dimensions.complexity(shape),
        "section_view": "Section" in views, "views": sorted(placed),
        "scale": placed["Front"]["scale"], "hidden_share": views["hidden_share"], "line_weights_mm": weights,
    }
    (out / "source.json").write_text(json.dumps(source, indent=1))
    (out / "dimensions.json").write_text(json.dumps(records, indent=1))
    return {"overlaps": sheet_render.overlaps(items)}


def _build_one(args):
    step_path, out, chunk = args
    try:
        return build_sheet(step_path, out, chunk)
    except Skip as skip:
        shutil.rmtree(out, ignore_errors=True)
        return {"skip": str(skip)}
    except Exception as error:
        shutil.rmtree(out, ignore_errors=True)
        return {"skip": f"error: {type(error).__name__}"}


def generate(out, seed):
    from cadgenbench.common.validity import analyze_step

    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    staging = out / "_built"
    ids = json.loads(abc_pool.CANDIDATE_IDS.read_text())["ids"]
    paths = abc_pool.step_paths()
    jobs = [(paths[i], staging / i, paths[i].parent.parent.name) for i in ids]
    skipped, overlaps, built = {}, {}, []
    with ProcessPoolExecutor(WORKERS) as pool:
        for i, result in zip(ids, pool.map(_build_one, jobs)):
            if "skip" in result:
                skipped[i] = result["skip"]
            else:
                built.append(i)
                overlaps[i] = result["overlaps"]
            print(f"{len(built)} built, {len(skipped)} skipped of {len(ids)}", flush=True)
    for i in list(built):
        if not analyze_step(staging / i / "exemplar.step").validation.is_valid:
            built.remove(i)
            overlaps.pop(i)
            skipped[i] = "invalid: analyze_step"
            shutil.rmtree(staging / i)
    test_ids, validation_ids = split(built, test_count(len(built)), seed)
    for group, group_ids in (("test", test_ids), ("validation", validation_ids)):
        (out / group).mkdir(parents=True)
        for i in group_ids:
            shutil.move(str(staging / i), str(out / group / i))
    shutil.rmtree(staging)
    (out / "split.json").write_text(json.dumps({
        "seed": seed, "source": abc_pool.CHUNKS, "test": test_ids, "validation": validation_ids,
        "skipped": skipped, "skip_counts": dict(Counter(skipped.values())),
        "overlaps": {i: o for i, o in overlaps.items() if o},
    }, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default="test-data")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    generate(args.out, args.seed)


if __name__ == "__main__":
    main()
