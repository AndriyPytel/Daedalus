"""
Prints `{"is_valid", "is_watertight", "errors"}` for the STEP named on the command line, from CADGenBench's own `analyze_step`.

It runs in the scorer's venv beside `harness/score.py`, not the suite's, and `analyze_step` spawns processes, so the call stays under the `__main__` guard. `tests/test_remote_acceptance.py` is its caller.
"""
import json
import sys


def main(path):
    from cadgenbench.common.validity import analyze_step

    verdict = analyze_step(path).validation
    print(json.dumps({"is_valid": verdict.is_valid, "is_watertight": verdict.is_watertight,
                      "errors": list(verdict.topology_errors)}))


if __name__ == "__main__":
    main(sys.argv[1])
