"""
Level 1 — model-facing prose lives in config/prompts/, not in code.

Text the provider receives — lint refusals, retry prompts, failure messages —
lives in config/prompts/ so correcting what the Copilot is told is a text edit
rather than a Python edit and a release.

A worker code template — a Python snippet `exec`'d in the Preflight Worker
against the Document, never sent to a provider — is named ending in `_CHECK`
by convention (`_ENVELOPE_CHECK`, `SELF_INTERSECTION_CHECK`, `_TOPOLOGY_CHECK`)
and is excluded on that name alone, so a new one needs no edit here.
"""
import ast
import os

from tests.source_tree import ROOT, package_dirs

THRESHOLD = 200


def _is_a_worker_code_template(name):
    return name.endswith("_CHECK")


def _module_level_string_constants(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if _is_a_worker_code_template(target.id):
                continue
            if not isinstance(node.value, ast.Constant):
                continue
            if not isinstance(node.value.value, str):
                continue
            if len(node.value.value) >= THRESHOLD:
                yield target.id, len(node.value.value), node.lineno


def test_no_oversized_prose_in_core():
    offenders = []
    for package_path in package_dirs():
        for dirpath, _, names in os.walk(package_path):
            if "__pycache__" in dirpath:
                continue
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(dirpath, name)
                where = os.path.relpath(full, ROOT)
                for var, length, line in _module_level_string_constants(full):
                    offenders.append("%s:%d %s (%d chars)" % (where, line, var, length))

    assert not offenders, (
        "model-facing prose belongs in config/prompts/, not in code: %s"
        % ", ".join(offenders))
