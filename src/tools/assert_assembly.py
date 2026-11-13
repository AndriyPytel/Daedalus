#!/usr/bin/env python
"""
The Assembly Assertion run over saved Documents, with no model: each path is opened in a kernel and the Assertion's refusals are printed.
Exit 0 where every Document holds the Assertion, 1 where one does not.

    .venv/bin/python src/tools/assert_assembly.py test-data/assembly/edit/*/reference.FCStd
"""
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))

for _lib in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.isdir(_lib) and _lib not in sys.path:
        sys.path.insert(0, _lib)


def verdicts(paths):
    """Why each saved Document in *paths* fails the Assertion, or `None` where it holds."""
    import sdk

    found = {}
    with sdk.Kernel(timeout=180) as kernel:
        for path in paths:
            opened = kernel.seed(os.path.abspath(path))
            found[path] = sdk.unfinished(kernel) if opened.ok else opened.traceback
    return found


def main():
    found = verdicts(sys.argv[1:])
    for path, why in found.items():
        print("%s: %s" % (path, "pass" if why is None else "FAIL " + why.replace("\n", " ")))
    return 0 if all(why is None for why in found.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
