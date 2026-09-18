#!/usr/bin/env python
"""
The addon-side caller for `harness/score.py`, the third harness script:
shells out to the vendored CADGenBench scorer's own venv so `cadgenbench`
itself is never imported here or anywhere under `src/`/`Mod/`.

    .venv/bin/python src/tools/score.py --candidate output.step \
        --exemplar manual/modelmania/exemplar/mm2025-phase1.step

`CADGENBENCH_VENV` names the harness venv's python; unset or missing, this
returns `None` rather than failing the run it is reporting on.
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS_SCRIPT = os.path.join(ROOT, "harness", "score.py")
DEFAULT_HARNESS_PYTHON = os.path.join(ROOT, "harness", ".venv", "bin", "python")


def harness_python():
    return os.environ.get("CADGENBENCH_VENV") or DEFAULT_HARNESS_PYTHON


def score(candidate_step, exemplar_step, python=None):
    """The vendored scorer's result dict for *candidate_step* vs *exemplar_step*.

    `None` when the harness venv named by `CADGENBENCH_VENV` (or the default
    `harness/.venv/bin/python`) does not exist, so a caller with no scorer
    installed degrades to no score rather than failing.
    """
    python = python or harness_python()
    if not os.path.exists(python):
        return None
    result = subprocess.run(
        [python, HARNESS_SCRIPT, "--candidate", candidate_step, "--exemplar", exemplar_step],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--exemplar", required=True)
    args = parser.parse_args()
    result = score(args.candidate, args.exemplar)
    if result is None:
        print("no harness venv found; set CADGENBENCH_VENV", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
