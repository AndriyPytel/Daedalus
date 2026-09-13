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
import re
import tokenize

from tests.source_tree import ROOT, sources, tracked

MACHINE_READABLE = ("# type:", "# noqa", "# pragma:", "# pylint:", "# flake8:",
                    "# fmt:", "# -*-", "#!", "# except-pass-allowed:")

TICKETS = ("FCC-", "ADR-")

TESTS = os.path.join(ROOT, "tests")


def suite_sources():
    """`(path relative to ROOT, source text)` for every module the suite itself carries."""
    for path, dirnames, names in os.walk(TESTS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(path, name)
            with open(full, encoding="utf-8") as handle:
                yield os.path.relpath(full, ROOT).replace(os.sep, "/"), handle.read()


def comments():
    for where, source in sources():
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


NOT_A_DOCUMENT = {".venv", ".git", "logs", "manual"}


def prose_files():
    known = tracked()
    for name in sorted(os.listdir(ROOT)):
        if name in NOT_A_DOCUMENT or name.startswith("."):
            continue
        full = os.path.join(ROOT, name)
        if os.path.isfile(full) and name.endswith(".md"):
            if name in known:
                yield os.path.relpath(full, ROOT)
        elif os.path.isdir(full) and name != "tests":
            for path, dirnames, names in os.walk(full):
                dirnames[:] = [d for d in dirnames if d not in NOT_A_DOCUMENT]
                for filename in sorted(names):
                    if filename.endswith((".md", ".py")):
                        where = os.path.relpath(os.path.join(path, filename), ROOT)
                        if where.replace(os.sep, "/") in known:
                            yield where


def test_adr_0021_is_never_cited_alone():
    """
    A docstring or a doc that quotes ADR-0021's single-call contract
    survives a partial revert or a copy-paste from an older commit unless
    something reads the prose. Nothing else does.
    """
    offenders = []
    for where in prose_files():
        with open(os.path.join(ROOT, where), encoding="utf-8") as handle:
            text = handle.read()
        if "ADR-0021" in text and "ADR-0027" not in text:
            offenders.append(where)

    assert not offenders, (
        "ADR-0021 is superseded by ADR-0027 (FCC-A-53): a file citing ADR-0021 "
        "must also cite ADR-0027, either as its replacement or to say what of "
        "ADR-0021 still holds: %s" % ", ".join(offenders))


HISTORY_MARKERS = ("used to", "now instead", "no longer", "replaces the old", "which is now")

DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstrings_in(source_iter):
    for where, source in source_iter:
        for node in ast.walk(ast.parse(source, filename=where)):
            if isinstance(node, DOCUMENTED):
                doc = ast.get_docstring(node)
                if doc:
                    yield where, getattr(node, "lineno", 1), node, doc


def docstrings():
    yield from docstrings_in(sources())


SENTENCE_END = re.compile(r"[.!?](?:\s|$)")
TICKET_ID = re.compile(r"(ADR|FCC)-\d+")


def test_no_docstring_holds_more_than_three_sentences():
    offenders = ["%s::%s (%d sentences)" % (where, getattr(node, "name", "module"), count)
                 for where, line, node, doc in docstrings()
                 for count in [len(SENTENCE_END.findall(doc.strip()))]
                 if count > 3]

    assert not offenders, (
        "a docstring over three sentences is a design document, and belongs "
        "in the knowledge base instead: %s" % ", ".join(offenders))


def test_no_docstring_carries_a_ticket_or_adr_id():
    offenders = ["%s::%s %s" % (where, getattr(node, "name", "module"), match.group())
                 for where, line, node, doc in docstrings()
                 for match in [TICKET_ID.search(doc)]
                 if match]

    assert not offenders, (
        "an id is not an explanation, it sends the reader elsewhere to learn "
        "what the docstring in front of them means -- say the constraint "
        "itself, in words: %s" % ", ".join(offenders))


def test_no_test_docstring_carries_a_ticket_or_adr_id():
    """A test is read more often than the code it covers -- the same rule holds here."""
    offenders = ["%s::%s %s" % (where, getattr(node, "name", "module"), match.group())
                 for where, line, node, doc in docstrings_in(suite_sources())
                 for match in [TICKET_ID.search(doc)]
                 if match]

    assert not offenders, (
        "an id is not an explanation, it sends the reader elsewhere to learn "
        "what the docstring in front of them means -- say the constraint "
        "itself, in words: %s" % ", ".join(offenders))


def offenders():
    found = {}
    for where, line, node, doc in docstrings():
        node_name = getattr(node, "name", "module")
        if any(marker in doc.lower() for marker in HISTORY_MARKERS):
            found["%s::%s" % (where, node_name)] = doc
    return found


def test_no_docstring_carries_a_ticket_history_marker():
    offenders_found = sorted(offenders())

    assert not offenders_found, (
        "a docstring narrating what used to happen is a commit message in "
        "the source, one syntax layer over the banned comment: %s"
        % ", ".join(offenders_found))
