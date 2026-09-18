"""
The vendored CADGenBench scorer is a harness dependency, installed only into
`harness/.venv` (Python 3.12, OCP/trimesh/manifold3d/open3d/pyvista) --
never into `.venv`, the addon's own Python 3.11 environment, and never
importable from anything the addon ships.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
MOD = os.path.join(ROOT, "Mod")


def _py_files(base):
    for path, _, names in os.walk(base):
        if "__pycache__" in path:
            continue
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(path, name)


def _all_imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _cadgenbench_imports(base):
    for full in _py_files(base):
        where = os.path.relpath(full, ROOT)
        with open(full, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=full)
        for module in _all_imports(tree):
            if module == "cadgenbench" or module.startswith("cadgenbench."):
                yield "%s imports %s" % (where, module)


def test_the_vendored_scorer_is_not_importable_from_src():
    offenders = list(_cadgenbench_imports(SRC))

    assert not offenders, "cadgenbench is a harness-only dependency: %s" % ", ".join(offenders)


def test_the_vendored_scorer_is_not_importable_from_mod():
    offenders = list(_cadgenbench_imports(MOD))

    assert not offenders, "cadgenbench is a harness-only dependency: %s" % ", ".join(offenders)


def test_cadgenbench_is_not_in_the_addons_own_venv_requirements():
    for name in ("pyproject.toml", "requirements.txt"):
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            assert "cadgenbench" not in handle.read().lower()
