"""
Scores a candidate Assembly document against a reference one, deterministically. The score is 0
when `solve()` fails or a reference part is missing; otherwise the joint match ratio (type and part
pair) times the mean of the placement score and 1 minus the interference volume share (up to 1e-6 is
boolean noise). Placement averages, per part, the centre and bounding-box error over the reference
diagonal and the rotation angle to the reference relative to the grounded part, over 180 degrees.
"""
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
FREECADCMD = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
SCRIPT = HARNESS / "assembly_freecad.py"
PLACEMENT_TOLERANCE = 0.1
INTERFERENCE_NOISE = 1e-6


def run_freecadcmd(job, script=SCRIPT):
    with tempfile.TemporaryDirectory() as work:
        job_path, result_path = Path(work) / "job.json", Path(work) / "result.json"
        job_path.write_text(json.dumps(job, default=str))
        env = {**os.environ, "DAE_JOB": str(job_path), "DAE_RESULT": str(result_path)}
        subprocess.run([FREECADCMD, "-c", f"exec(open({str(script)!r}).read())"], env=env, check=True, capture_output=True)
        return json.loads(result_path.read_text())


def build(jobs):
    """Write each (spec, path) as a solved-in-place Assembly document."""
    run_freecadcmd({"build": [[spec, str(path)] for spec, path in jobs]})


def measure(paths):
    """Solve and measure each document: solve status, per-part bounds, joints, interference volume."""
    return run_freecadcmd({"measure": [str(path) for path in paths]})


def _placement_error(candidate, reference, diagonal):
    centre = sum((c - r) ** 2 for c, r in zip(candidate["com"], reference["com"])) ** 0.5
    bounds = sum(abs(c - r) for c, r in zip(candidate["bbox"], reference["bbox"])) / 6
    return (centre + bounds) / 2 / diagonal


def _rotation_error(candidate, reference):
    dot = abs(sum(c * r for c, r in zip(candidate["rot"], reference["rot"])))
    return 2 * math.acos(min(1.0, dot)) / math.pi


def _joint_keys(measurement):
    return sorted((j["type"], *sorted((j["a"], j["b"]))) for j in measurement["joints"])


def combine(candidate, reference):
    """Score two measurements; every component is in [0, 1]."""
    zero = {"score": 0.0, "solve": candidate["solve"], "placement": 0.0, "joints": 0.0, "interference": 0.0}
    if candidate["solve"] != 0 or set(reference["parts"]) - set(candidate["parts"]):
        return zero
    count = max(len(reference["parts"]), len(candidate["parts"]))
    placement = sum(
        (
            max(0.0, 1 - _placement_error(candidate["parts"][label], part, reference["diagonal"]) / PLACEMENT_TOLERANCE)
            + 1 - _rotation_error(candidate["parts"][label], part)
        ) / 2
        for label, part in reference["parts"].items()
    ) / count
    wanted, found = _joint_keys(reference), _joint_keys(candidate)
    matched = sum(min(wanted.count(key), found.count(key)) for key in set(wanted))
    joints = matched / max(len(wanted), len(found)) if wanted or found else 1.0
    volume = sum(part["volume"] for part in candidate["parts"].values())
    share = candidate["interference"] / volume
    interference = 1.0 if share <= INTERFERENCE_NOISE else max(0.0, 1 - share)
    return {
        "score": joints * (placement + interference) / 2,
        "solve": 0,
        "placement": placement,
        "joints": joints,
        "interference": interference,
    }


def score(candidate_path, reference_path):
    candidate, reference = measure([candidate_path, reference_path])
    return combine(candidate, reference)
