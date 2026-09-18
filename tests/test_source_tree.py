"""
The derivation in `source_tree` is checked against the tree it derives from.

A guard that scans a hand-written list of packages goes stale silently: three
of them here held a tuple written before `copilot/` existed and stayed green
for a whole epic. Deriving the list only moves the problem unless something
fails when the derivation misses a file, which is what this does.

`Init.py` and `InitGui.py` are the two modules FreeCAD's loader finds by name
at the addon root. They are not in a package and are named here so that a
third file appearing beside them is a failure rather than a silence.
"""
import os

from tests.source_tree import ROOT, sources

NOT_IN_A_PACKAGE = {"Init.py", "InitGui.py"}

OFF_THE_ADDON = {"tests", "manual", "logs", "docs", ".git", ".venv", "__pycache__", "harness"}


def every_python_file():
    for path, dirnames, names in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in OFF_THE_ADDON and not d.startswith(".")]
        for name in names:
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(path, name), ROOT).replace(os.sep, "/")


def test_the_guards_read_every_module_the_addon_ships():
    unread = sorted(set(every_python_file()) - {path for path, _ in sources()})

    assert set(unread) <= NOT_IN_A_PACKAGE, (
        "these modules ship with the addon and no guard reads them: %s"
        % ", ".join(name for name in unread if name not in NOT_IN_A_PACKAGE))
