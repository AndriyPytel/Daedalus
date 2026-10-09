"""
Score one candidate STEP against one exemplar STEP with the vendored
CADGenBench scorer -- run only inside its own venv, never inside `.venv`
or FreeCAD's Python. Build that venv once, on macOS:

    brew install libusb
    uv venv --python 3.12 harness/.venv
    uv pip install --python harness/.venv/bin/python \
        "cadgenbench @ git+https://github.com/huggingface/cadgenbench.git"

Then run this script with `harness/.venv/bin/python`, never with
`.venv/bin/python` (Python 3.11, no OCP/trimesh/open3d) or FreeCAD's own
Python. `src/tools/score.py` is the addon-side caller: it shells out to
whichever interpreter `CADGENBENCH_VENV` names (default
`harness/.venv/bin/python`) and reads this script's JSON on stdout, so
`cadgenbench` itself is never imported from `src/` or `Mod/`.

Shape similarity and the CAD validity gate are CADGenBench's own
(`cadgenbench.eval.evaluate.evaluate_result`); topology is not --
`core.topology.betti_numbers` runs here against this script's own
tessellation of the *raw* (pre-alignment) candidate and reference, the same
function `core.topology` also hands FreeCAD's tessellation to, so the two
scorers agree on what a Betti number is. The final `score` is this repo's
own weighting, `0.7 * shape_similarity + 0.3 * topology`, zeroed by the
validity gate exactly as CADGenBench's own `cad_score` is -- never
CADGenBench's own weighted mean, and never comparable to its leaderboard.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, _SRC)

SHAPE_WEIGHT = 0.7
TOPOLOGY_WEIGHT = 0.3


def score(candidate_step, exemplar_step):
    """Score one candidate against one exemplar; see the module docstring for the formula."""
    from cadgenbench.common.artifacts import StepArtifacts
    from cadgenbench.common.mesh import deflection_for_bbox
    from cadgenbench.eval.evaluate import evaluate_result

    import sdk

    candidate_step = Path(candidate_step)
    exemplar_step = Path(exemplar_step)

    with tempfile.TemporaryDirectory() as tmp:
        result_dir = Path(tmp) / "result"
        gt_dir = Path(tmp) / "gt"
        result_dir.mkdir()
        gt_dir.mkdir()
        (result_dir / "output.step").write_bytes(candidate_step.read_bytes())
        (gt_dir / "ground_truth.step").write_bytes(exemplar_step.read_bytes())

        shape_scores = evaluate_result(result_dir, gt_dir)
        result = json.loads((result_dir / "result.json").read_text())

    is_valid = result.get("validation", {}).get("is_valid", False)
    is_watertight = result.get("validation", {}).get("is_watertight", False)

    if not is_valid:
        return {
            "score": 0.0,
            "is_valid": is_valid,
            "is_watertight": is_watertight,
            "shape_similarity_score": None,
            "topology_score": None,
            "candidate_betti": None,
            "exemplar_betti": None,
        }

    gt_artifacts = StepArtifacts(exemplar_step, is_ground_truth=True)
    deflection = deflection_for_bbox(gt_artifacts.analysis.measurements.bounding_box.diagonal)
    candidate_artifacts = StepArtifacts(candidate_step, deflection_override=deflection)

    exemplar_mesh = gt_artifacts.mesh()
    candidate_mesh = candidate_artifacts.mesh()
    exemplar_betti = sdk.betti_numbers(exemplar_mesh.vertices, exemplar_mesh.triangles)
    candidate_betti = sdk.betti_numbers(candidate_mesh.vertices, candidate_mesh.triangles)
    topology = sdk.topology_score(candidate_betti, exemplar_betti)
    shape_similarity = shape_scores["shape_similarity_score"]

    return {
        "score": round(SHAPE_WEIGHT * shape_similarity + TOPOLOGY_WEIGHT * topology, 4),
        "is_valid": is_valid,
        "is_watertight": is_watertight,
        "shape_similarity_score": round(shape_similarity, 4),
        "topology_score": round(topology, 4),
        "candidate_betti": candidate_betti,
        "exemplar_betti": exemplar_betti,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--exemplar", required=True)
    args = parser.parse_args()
    print(json.dumps(score(args.candidate, args.exemplar)))


if __name__ == "__main__":
    main()
