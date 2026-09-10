"""
FreeCAD puts the installed addon's directory on the import path. FCC-362:
while `Mod` symlinked at a second checkout, `core.sheets.sheet_reader` and
`core.sheets.sketch_check` kept resolving from that checkout's flat `core`,
so a call site asking for a name this branch had moved found it anyway and
the suite stayed green on the wrong tree. This is the check that would have
caught it: every one of our top-level packages already imported in a run,
named against the checkout it should have come from.

Covers the namespace-package case deliberately -- `core` ships with no
`__init__.py`, so the package object carries `__path__` and no `__file__`,
which is exactly how it presented itself while it was being shadowed: a
second directory of the same name, earlier on `sys.path`, merges into the
same namespace package rather than failing to import.
"""
import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUR_PACKAGES = ("core", "copilot", "bridge", "ui", "tools", "Mod")


def _origins(module):
    """Every path a module could have come from: `__file__`, or a namespace package's `__path__`."""
    origin = getattr(module, "__file__", None)
    if origin:
        return [origin]
    return list(getattr(module, "__path__", []))


def foreign_modules(modules, root):
    """Names and paths of already-imported modules of ours not served from `root`."""
    offenders = []
    for name, module in modules.items():
        if module is None or name.split(".")[0] not in OUR_PACKAGES:
            continue
        for origin in _origins(module):
            if os.path.commonpath([root, os.path.abspath(origin)]) != root:
                offenders.append("%s (%s)" % (name, origin))
    return offenders


def test_every_module_of_ours_already_imported_came_from_this_checkout():
    offenders = foreign_modules(sys.modules, ROOT)
    assert not offenders, offenders


def test_a_module_shadowed_from_elsewhere_is_named_with_its_foreign_path(tmp_path):
    shadow = tmp_path / "shadow"
    (shadow / "core").mkdir(parents=True)
    (shadow / "core" / "marker.py").write_text("")

    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, {shadow!r})
        sys.path.insert(1, {src!r})
        import core
        sys.path.insert(0, {tests!r})
        from test_modules_come_from_this_checkout import foreign_modules
        print(foreign_modules(sys.modules, {root!r}))
    """).format(shadow=str(shadow), src=os.path.join(ROOT, "src"),
                tests=os.path.dirname(os.path.abspath(__file__)), root=ROOT)

    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr

    offenders = result.stdout.strip()
    assert "core" in offenders, offenders
    assert str(shadow) in offenders, offenders
