"""
The import boundary FCC-317 built stays shut (FCC-318), plus one more rule:
nothing outside the harness directory may import from it.

FCC-317 repointed the panel to the SDK but could not finish the job: four
wiring imports inside `ui/copilot_panel.py`'s `LoopWorker` still reach into
`core.*` on purpose, because the SDK does not yet offer live-document mode,
injectable dispatch/confirm, no-forced-kernel, or outstanding passthrough
(see FCC-317, FCC-315). Those four are named here, not silently allowed: each
line names the ticket that owns removing it, so the exception list shrinks
ticket by ticket instead of growing back invisibly. Any other `core` import
anywhere under `ui/`, present or future, fails this test.

The harness directory's two scripts drive `copilot.sdk.Client` the same way
any other caller does, so nothing else in the repository has a reason to
reach into it either; a stray import of that package is caught the same
way a stray `core` import under `ui/` is.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")

KNOWN_EXCEPTIONS = {
    ("ui/copilot_panel.py", "core.llm"): "FCC-317/FCC-315: SDK has no live-document mode",
    ("ui/copilot_panel.py", "core.freecad.apply"): "FCC-317/FCC-315: SDK has no injectable dispatch/confirm",
    ("ui/copilot_panel.py", "core.freecad.freecad_tools"): "FCC-317/FCC-315: SDK has no no-forced-kernel mode",
    ("ui/copilot_panel.py", "core.loop"): "FCC-317/FCC-315: SDK has no outstanding passthrough",
}


def core_imports():
    for path, _, names in os.walk(UI_DIR):
        if "__pycache__" in path:
            continue
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(path, name)
            where = os.path.relpath(full, ROOT)
            with open(full, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=full)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "core" or node.module.startswith("core."):
                        yield where, node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "core" or alias.name.startswith("core."):
                            yield where, alias.name


def test_ui_does_not_import_core_except_known_wiring_gaps():
    offenders = [
        "%s imports %s" % (where, module)
        for where, module in core_imports()
        if (where, module) not in KNOWN_EXCEPTIONS
    ]

    assert not offenders, (
        "ui must not import core.* outside the named FCC-317 exceptions: %s"
        % ", ".join(offenders))


HARNESS_CALLER_DIRS = ("core", "ui", "Mod", "utils", "tests", "copilot")


def harness_imports():
    for package in HARNESS_CALLER_DIRS:
        package_dir = os.path.join(ROOT, package)
        if not os.path.isdir(package_dir):
            continue
        for path, _, names in os.walk(package_dir):
            if "__pycache__" in path:
                continue
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(path, name)
                where = os.path.relpath(full, ROOT)
                with open(full, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), filename=full)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module == "tools" or node.module.startswith("tools."):
                            yield where, node.module
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == "tools" or alias.name.startswith("tools."):
                                yield where, alias.name


def test_nothing_outside_the_harness_directory_imports_it():
    offenders = ["%s imports %s" % (where, module) for where, module in harness_imports()]

    assert not offenders, "only the harness scripts may import each other: %s" % ", ".join(offenders)
