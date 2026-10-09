"""
The names listed here are gone from src, Mod and tests, and nothing in the tree calls it.
A name is judged by the file that defined it; one that another file also defines is not flagged where it is only referenced.
"""
import ast
import os

from tests.source_tree import ROOT, tracked

DELETED_FILES = (
    "config/prompts/messages/asked_again.md",
    "src/core/logs_root.py",
    "tests/core/test_logs_root.py",
    "tests/core/kernel/level2/test_traps_level2.py",
)

DELETED_NAMES = {
    "src/core/loop/prompt.py": ("_ask", "_streamed", "_one_call", "_asked_again", "_ASKED_AGAIN", "_as_json"),
    "src/core/agent/subagent.py": ("_free_ram_mb", "default_concurrency", "MB_PER_KERNEL", "active_kernels",
                                   "release", "phase_depth"),
    "src/core/agent/agent.py": ("_next_step", "_previous_step"),
    "src/core/loop/assertion.py": ("_consulted_design",),
    "src/core/events.py": ("CONSULT_DESIGN", "CONSULT_DESIGN_ANSWERED", "first", "of", "as_log", "__getitem__"),
    "src/core/freecad_traps.py": ("verify_trap", "verify_seed_traps", "TrapEvidence", "VERIFIED_PASS",
                                  "VERIFIED_FAIL", "UNVERIFIED"),
    "src/core/design/gaps.py": ("measured_from_what_is_gone", "carried_by_no_source"),
    "src/core/design/core.py": ("canonical_place",),
    "src/core/design/report.py": ("_worth_a_notice",),
    "src/core/kernel/kernel_worker.py": ("_bodies_in_pieces", "_bodies_with_invalid_shape",
                                         "_bodies_with_inverted_solid", "_bodies_with_non_manifold_mesh"),
    "Mod/ui/queue_widget.py": ("_repaint_steps", "phase_state", "step_state", "step_count"),
    "src/core/sheets/sheet_reader.py": ("_box_covers", "INK_OF_THE_BOX"),
    "Mod/ui/daedalus_dock.py": ("forget",),
}

DELETED_MODULES = ("core.logs_root",)


def _trees():
    for path in sorted(tracked()):
        if path.endswith(".py") and path.split("/")[0] in ("src", "Mod", "tests"):
            with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
                yield path, ast.parse(handle.read())


def _defined(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
    return names


def _referenced(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id, node.lineno
        elif isinstance(node, ast.Attribute):
            yield node.attr, node.lineno
        elif isinstance(node, ast.alias):
            yield node.name.split(".")[-1], getattr(node, "lineno", 0)


def test_the_deleted_files_are_gone():
    assert [path for path in DELETED_FILES if os.path.exists(os.path.join(ROOT, path))] == []


def test_no_deleted_name_is_defined_where_it_was():
    trees = dict(_trees())
    found = [(path, name) for path, names in DELETED_NAMES.items() for name in names
             if path in trees and name in _defined(trees[path])]
    assert found == []


def test_no_deleted_name_is_used_anywhere():
    trees = dict(_trees())
    defined_in = {path: _defined(tree) for path, tree in trees.items()}
    found = []
    for owner, names in DELETED_NAMES.items():
        for name in names:
            elsewhere = any(name in defined for path, defined in defined_in.items() if path != owner)
            if elsewhere:
                continue
            for path, tree in trees.items():
                found += [(path, line, name) for used, line in _referenced(tree) if used == name]
    assert found == []


def test_no_deleted_module_is_imported():
    found = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in DELETED_MODULES:
                found.append((path, node.lineno, node.module))
    assert found == []


def test_review_names_no_source_that_its_phase_contract_does_not_declare():
    trees = dict(_trees())
    found = [node.lineno for node in ast.walk(trees["src/core/loop/review.py"]) if isinstance(node, ast.Dict)
             and any(isinstance(key, ast.Constant) and key.value == "description" for key in node.keys)]
    assert found == []


def test_the_logs_directory_sits_beside_the_root_in_prompts():
    trees = dict(_trees())
    assert "LOGS" in _defined(trees["src/core/prompts.py"])
