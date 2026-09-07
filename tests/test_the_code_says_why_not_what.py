"""
Level 1 — the code says why, never what (FCC-129).

A comment is the one place in this repo where a claim about the code sits
beside the code without anything checking it is still true. Measured before
this test was written, `core` alone carried 921 comment lines, 237 of them in
multi-line blocks, and 71 mentions of a ticket id — commit messages copied into
source, where the next reader cannot tell which of them the code still obeys.

The rule is in `AGENTS.md`. This test is here because the rule was already
written down once, in the global agent config, and every review since `906002a`
found it broken again: an agent reads the instruction once and reads the
surrounding code every time it writes a line. The surrounding code has to agree
with the instruction, and only a test keeps it agreeing.

A measurement or a constraint is not lost by this — it moves into the docstring
of the thing it describes, which is where a reader goes looking for it and
where `help()` will show it.

Read with `tokenize` rather than `ast`: comments are not in the tree.
"""
import ast
import io
import os
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEARCHED = ("core", "ui", "utils", "tools", "Mod")

MACHINE_READABLE = ("# type:", "# noqa", "# pragma:", "# pylint:", "# flake8:",
                    "# fmt:", "# -*-", "#!")

TICKETS = ("FCC-", "ADR-")


def comments():
    for directory in SEARCHED:
        for path, _, names in os.walk(os.path.join(ROOT, directory)):
            if "__pycache__" in path:
                continue
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(path, name)
                with open(full, encoding="utf-8") as handle:
                    source = handle.read()
                where = os.path.relpath(full, ROOT)
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type == tokenize.COMMENT:
                        yield where, token.start[0], token.string.strip()


def prose():
    for where, line, text in comments():
        if text.startswith(MACHINE_READABLE):
            continue
        yield where, line, text


def test_no_module_explains_itself_in_a_comment():
    offenders = ["%s:%d %s" % found for found in prose()]

    assert not offenders, (
        "a measurement or a constraint belongs in the docstring of what it "
        "describes, and nothing else belongs in a comment: %s"
        % ", ".join(offenders))


def test_no_comment_carries_a_ticket_id():
    offenders = ["%s:%d %s" % (where, line, text)
                 for where, line, text in comments()
                 if any(ticket in text for ticket in TICKETS)]

    assert not offenders, (
        "a ticket id in a comment is a commit message in the source: %s"
        % ", ".join(offenders))


DOCSTRING_BUDGET = {ast.Module: 20, ast.ClassDef: 8, ast.FunctionDef: 8, ast.AsyncFunctionDef: 8}
PRIVATE_BUDGET = 5
HISTORY_MARKERS = ("used to", "now instead", "no longer", "replaces the old", "which is now")

DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstrings():
    for directory in SEARCHED:
        for path, _, names in os.walk(os.path.join(ROOT, directory)):
            if "__pycache__" in path:
                continue
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(path, name)
                with open(full, encoding="utf-8") as handle:
                    source = handle.read()
                where = os.path.relpath(full, ROOT)
                tree = ast.parse(source, filename=full)
                for node in ast.walk(tree):
                    if isinstance(node, DOCUMENTED):
                        doc = ast.get_docstring(node)
                        if doc:
                            yield where, getattr(node, "lineno", 1), node, doc


def test_no_docstring_exceeds_its_budget():
    offenders = []
    for where, line, node, doc in docstrings():
        node_name = getattr(node, "name", "module")
        budget = PRIVATE_BUDGET if node_name.startswith("_") else DOCSTRING_BUDGET[type(node)]
        length = doc.count("\n") + 1
        if length > budget:
            offenders.append("%s:%d %s (%d lines, budget %d)"
                              % (where, line, node_name, length, budget))

    assert not offenders, (
        "a docstring over budget is narrative or duplication that belongs "
        "elsewhere, same as a banned comment: %s" % ", ".join(offenders))


def test_no_docstring_carries_a_ticket_history_marker():
    offenders = ["%s:%d %s" % (where, line, getattr(node, "name", "module"))
                 for where, line, node, doc in docstrings()
                 if any(marker in doc.lower() for marker in HISTORY_MARKERS)]

    assert not offenders, (
        "a docstring narrating what used to happen is a commit message in "
        "the source, one syntax layer over the banned comment: %s"
        % ", ".join(offenders))
