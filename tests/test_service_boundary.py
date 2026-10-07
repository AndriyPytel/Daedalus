"""
The gateway process holds no FreeCAD and no engine, and the worker reaches the engine through `sdk.Client` alone.

The module list is walked from `src/service/`, so a new file is covered without editing this test, and a second
assertion proves the walk equals the files on disk. `violations` is pure over source text so the rule itself is tested.
"""
import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE = os.path.join(ROOT, "src", "service")
ENGINE_ROOTS = {"FreeCAD", "FreeCADGui", "Part", "PySide", "PySide2", "PySide6", "core", "tools", "sdk", "Mod", "bridge", "ui"}
WORKER = "worker.py"


def service_modules():
    for path, dirnames, names in os.walk(SERVICE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(path, name)


def violations(source, role):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            pairs = [(alias.name, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            pairs = [(node.module, [alias.name for alias in node.names])]
        else:
            continue
        for module, names in pairs:
            top = module.split(".")[0]
            if top not in ENGINE_ROOTS:
                continue
            if role == "worker" and module == "sdk" and names == ["Client"]:
                continue
            found.append(module)
    return found


def role_of(path):
    return "worker" if os.path.basename(path) == WORKER and os.path.dirname(path) == SERVICE else "gateway"


def test_the_service_package_exists():
    assert os.path.isfile(os.path.join(SERVICE, "__init__.py"))
    assert os.path.isfile(os.path.join(SERVICE, WORKER))


def test_no_service_module_crosses_the_boundary():
    offenders = []
    for path in service_modules():
        with open(path, encoding="utf-8") as handle:
            for module in violations(handle.read(), role_of(path)):
                offenders.append("%s imports %s" % (os.path.relpath(path, ROOT), module))
    assert offenders == []


def test_the_walk_covers_every_file_on_disk():
    on_disk = glob.glob(os.path.join(SERVICE, "**", "*.py"), recursive=True)
    assert sorted(service_modules()) == sorted(p for p in on_disk if "__pycache__" not in p)


def test_a_gateway_module_importing_freecad_or_sdk_is_caught():
    for line in ("import FreeCAD", "from FreeCAD import Part", "import sdk", "from sdk import Client", "from core import loop"):
        assert violations(line, "gateway"), line


def test_the_worker_may_import_the_client_and_nothing_else_of_the_engine():
    assert violations("from sdk import Client", "worker") == []
    for line in ("import sdk", "from sdk import Client, Other", "from core.loop import execute", "import FreeCAD", "from tools import lane"):
        assert violations(line, "worker"), line


def test_a_nested_worker_py_is_a_gateway_module():
    assert role_of(os.path.join(SERVICE, "sub", WORKER)) == "gateway"
