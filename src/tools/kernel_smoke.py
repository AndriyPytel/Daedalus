#!/usr/bin/env python
"""
Smoke-check the kernel: run one Box through it and print what came back.

    .venv/bin/python src/tools/kernel_smoke.py "doc.addObject('Part::Box', 'Box')"
"""
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(_SRC))

from core.kernel.kernel import Kernel


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "doc.addObject('Part::Box', 'Box')"
    with Kernel() as kernel:
        result = kernel.check(code, render_path="/tmp/kernel_smoke.png")
        print("ok        :", result.ok)
        print("objects   :", [o["name"] for o in result.objects])
        print("render    :", result.render)
        if result.traceback:
            print(result.traceback)
    return 0


if __name__ == "__main__":
    sys.exit(main())
