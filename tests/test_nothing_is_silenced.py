"""
Level 1 — a warning is removed by removing its cause (FCC-24, FCC-28).

`warnings.filterwarnings` was ruled out explicitly at interview FCC-23: a
filter is not a fix, it is the same defect with the evidence turned off. This
is a test rather than a review note because a filter is one line and it comes
back the moment something is noisy on a deadline.

Checked by parsing, so a filter written in a module that needs FreeCAD to
import is caught just the same.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEARCHED = ("core", "ui", "utils", "Mod", "tools")

SILENCERS = {
    ("warnings", "filterwarnings"),
    ("warnings", "simplefilter"),
    ("warnings", "catch_warnings"),
}


def sources():
    for directory in SEARCHED:
        for path, _, names in os.walk(os.path.join(ROOT, directory)):
            if "__pycache__" in path:
                continue
            for name in sorted(names):
                if name.endswith(".py"):
                    full = os.path.join(path, name)
                    with open(full, encoding="utf-8") as handle:
                        yield os.path.relpath(full, ROOT), handle.read()


def test_no_module_silences_a_warning():
    offenders = []
    for path, source in sources():
        tree = ast.parse(source, path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if (func.value.id, func.attr) in SILENCERS:
                    offenders.append("%s:%d %s.%s" % (path, node.lineno,
                                                      func.value.id, func.attr))
            elif isinstance(func, ast.Name) and func.id in {
                    "filterwarnings", "simplefilter"}:
                offenders.append("%s:%d %s" % (path, node.lineno, func.id))

    assert not offenders, (
        "a warning is removed by removing its cause, never by filtering it: %s"
        % ", ".join(offenders))


def test_no_module_turns_python_warnings_off_through_the_environment():
    """`PYTHONWARNINGS=ignore` is the same decision spelled differently."""
    offenders = [path for path, source in sources()
                 if "PYTHONWARNINGS" in source]

    assert not offenders, "PYTHONWARNINGS is set in: %s" % ", ".join(offenders)
