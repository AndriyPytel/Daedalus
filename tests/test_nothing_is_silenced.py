"""
Level 1 — a warning is removed by removing its cause.

`warnings.filterwarnings` was ruled out explicitly at interview: a
filter is not a fix, it is the same defect with the evidence turned off. This
is a test rather than a review note because a filter is one line and it comes
back the moment something is noisy on a deadline.

Checked by parsing, so a filter written in a module that needs FreeCAD to
import is caught just the same.
"""
import ast
import re

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


EXCEPT_PASS_MARKER = re.compile(r"#\s*except-pass-allowed:\s*(\S.*)")


def test_a_swallowed_exception_is_named_or_carries_its_own_marker():
    """
    A swallowed transcript exception once cost five Sessions their whole log
    with no line anywhere naming why. A handler
    whose entire body is `pass` gets one of two things: a log line, or a
    trailing `# except-pass-allowed: <reason>` comment on the `pass` line
    itself, read here rather than kept as a separate list keyed by line
    number — a table an unrelated edit above it silently shifts stale.
    """
    offenders = []
    for path, source in sources():
        lines = source.splitlines()
        tree = ast.parse(source, path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ExceptHandler)
                    and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                continue
            line = lines[node.body[0].lineno - 1]
            matched = EXCEPT_PASS_MARKER.search(line)
            if not matched or not matched.group(1).strip():
                offenders.append("%s:%d" % (path, node.body[0].lineno))

    assert not offenders, (
        "a silent except:pass with no log line and no "
        "'# except-pass-allowed: <reason>' marker: %s" % ", ".join(offenders))
