"""
`run_phase` no longer returns a value no caller reads.

The bare `checkpoints` list that replaced it was unpacked at every call
site and read at none; this is the grep-level guard against that
regression. Every phase reaches `run_phase` through `run_declaring` alone.
"""
import os

from tests.source_tree import ROOT, tracked


def _callers():
    for relative in sorted(tracked()):
        if relative.startswith("src/core/") and relative.endswith(".py") \
                and relative != "src/core/agent/agent.py":
            with open(os.path.join(ROOT, relative), encoding="utf-8") as handle:
                yield relative, handle.read()
