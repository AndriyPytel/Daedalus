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

from tests.source_tree import sources

SILENCERS = {
    ("warnings", "filterwarnings"),
    ("warnings", "simplefilter"),
    ("warnings", "catch_warnings"),
}


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


ALLOWED_EXCEPT_PASS = {
    ("Mod/bridge/logger.py", 84),
    ("Mod/ui/accordion_widget.py", 190),
    ("Mod/ui/copilot_panel.py", 664),
    ("Mod/ui/ui_helpers.py", 148),
    ("Mod/ui/ui_helpers.py", 171),
    ("Mod/ui/ui_helpers.py", 185),
    ("Mod/ui/upload_widget.py", 280),
    ("src/core/dependency_manager.py", 110),
    ("src/core/events.py", 121),
    ("src/core/freecad_apply.py", 148),
    ("src/core/freecad_apply.py", 482),
    ("src/core/freecad_apply.py", 604),
    ("src/core/kernel/kernel.py", 185),
    ("src/core/kernel/kernel.py", 204),
    ("src/core/kernel/kernel_worker.py", 177),
    ("src/core/loop/execute.py", 553),
    ("src/core/loop/looks.py", 67),
    ("src/core/loop/looks.py", 70),
    ("src/core/sessions.py", 35),
}


def test_a_swallowed_exception_is_named_or_on_the_shrinking_allowlist():
    """
    FCC-390: `copilot_panel.py`'s transcript `except Exception: pass` cost
    five Sessions their whole log with no line anywhere naming why. A handler
    whose entire body is `pass` gets one of two things: a log line, or a
    place on this list. The list is exact, not a ceiling — fixing one without
    deleting its entry here fails this test, the same way adding a new one
    without either fixing it or listing it does.
    """
    offenders = set()
    for path, source in sources():
        tree = ast.parse(source, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    offenders.add((path, node.lineno))

    assert offenders == ALLOWED_EXCEPT_PASS, (
        "a silent except:pass appeared, or one already fixed is still listed: "
        "new=%s fixed=%s" % (offenders - ALLOWED_EXCEPT_PASS,
                             ALLOWED_EXCEPT_PASS - offenders))
