"""
The gateway process holds no FreeCAD and no engine, and the worker reaches the engine through the public names `sdk.py` defines itself.

The module list is walked from `src/service/`, so a new file is covered without editing this test, and a second
assertion proves the walk equals the files on disk. `violations` is pure over source text so the rule itself is tested.
"""
import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE = os.path.join(ROOT, "src", "service")
MOD = os.path.join(ROOT, "Mod")
OUTSIDE_THE_TREE = {"FreeCAD", "FreeCADGui", "Part", "PySide", "PySide2", "PySide6"}
WORKER = "worker.py"
SDK = os.path.join(ROOT, "src", "sdk.py")


def sdk_public_names():
    with open(SDK, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return {name for name in names if not name.startswith("_")}


def _top_names(base):
    return {os.path.splitext(name)[0] for name in os.listdir(base)
            if (name.endswith(".py") or os.path.isdir(os.path.join(base, name)))
            and not name.startswith(("_", "."))}


def engine_roots():
    return (_top_names(os.path.join(ROOT, "src")) - {"service"}) | _top_names(MOD) | {"Mod"} | OUTSIDE_THE_TREE


def service_modules():
    for path, dirnames, names in os.walk(SERVICE):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(path, name)


def violations(source, role):
    found = []
    roots = engine_roots()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            pairs = [(alias.name, None) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = "service." + module if module else "service"
            pairs = [(module, [alias.name for alias in node.names])]
        else:
            continue
        for module, names in pairs:
            top = module.split(".")[0]
            if role == "gateway" and (module == "service.worker" or module.startswith("service.worker.")
                                      or (module == "service" and "worker" in names)):
                found.append("service.worker")
                continue
            if top not in roots:
                continue
            if role == "worker" and module == "sdk" and names and set(names) <= sdk_public_names():
                continue
            found.append(module)
    return found


def role_of(path):
    return "worker" if os.path.basename(path) == WORKER and os.path.dirname(path) == SERVICE else "gateway"


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


def test_the_worker_may_import_what_sdk_defines_publicly_and_nothing_else_of_the_engine():
    assert violations("from sdk import Client, set_api_keys, set_last_model", "worker") == []
    for line in ("import sdk", "from sdk import Client, Other", "from sdk import run", "from sdk import _settings",
                 "from core.loop import execute", "import FreeCAD", "from tools import lane"):
        assert violations(line, "worker"), line


def test_the_public_names_are_the_ones_sdk_defines_and_not_the_ones_it_imports():
    names = sdk_public_names()
    assert {"Client", "set_api_keys", "set_base_urls", "set_last_model", "set_effort"} <= names
    assert not names & {"run", "Session", "events", "route", "Kernel"}


def test_a_nested_worker_py_is_a_gateway_module():
    assert role_of(os.path.join(SERVICE, "sub", WORKER)) == "gateway"


def test_a_gateway_module_importing_the_worker_is_caught():
    for line in ("from service import worker", "import service.worker", "from service.worker import Client",
                 "from . import worker", "from .worker import Client"):
        assert violations(line, "gateway"), line
    assert violations("from service.app import app", "gateway") == []


def test_the_engine_roots_cover_every_top_level_name_under_src_and_mod():
    roots = engine_roots()
    for base in ("src", "Mod"):
        for name in os.listdir(os.path.join(ROOT, base)):
            stem = os.path.splitext(name)[0]
            if stem in ("service", "__pycache__") or stem.startswith((".", "_")):
                continue
            assert stem in roots, stem


def test_the_package_init_imports_nothing():
    with open(os.path.join(SERVICE, "__init__.py"), encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    assert [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))] == []
