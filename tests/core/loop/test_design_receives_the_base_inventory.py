"""
Level 1 -- an editing run hands Design the text inventory of the base beside
the edit sentence; Design is never given a Document or a FreeCAD call.
"""
import ast

import pytest

from core.events import EventStream
from core.loop import design as design_module
from core.loop import describe as describe_module
from core.loop import run as run_module
from core.loop.budgets import Budgets
from core.loop.results import Stopped
from core.tools import ToolRegistry

INVENTORY = "bbox 434.8 x 274.0 x 91.6 mm; Cone d=279 axis Z; 32 holes d=12"


def test_first_pass_puts_the_inventory_in_the_design_body(monkeypatch):
    bodies = []
    monkeypatch.setattr(design_module, "design",
                        lambda body, *args, **kwargs: bodies.append(body) or ({}, ""))

    design_module._first_pass("make the flange 10 mm thinner", None, EventStream(), [],
                              lambda: False, base=INVENTORY)

    assert [body["the_base_part"] for body in bodies] == [INVENTORY]
    assert bodies[0]["intent"] == "make the flange 10 mm thinner"


def test_a_run_without_a_base_sends_no_the_base_part_key(monkeypatch):
    bodies = []
    monkeypatch.setattr(design_module, "design",
                        lambda body, *args, **kwargs: bodies.append(body) or ({}, ""))

    design_module._first_pass("a box", None, EventStream(), [], lambda: False)

    assert "the_base_part" not in bodies[0]


def test_the_run_reads_the_inventory_off_the_tools_and_hands_it_to_design(monkeypatch):
    registry = ToolRegistry()
    registry.tool("base_inventory", "The base part, in words.")(
        lambda: {"inventory": INVENTORY})
    seen = {}

    def describing(*args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        raise Stopped()

    monkeypatch.setattr(run_module, "_describe_the_part", describing)

    with pytest.raises(Stopped):
        run_module._run("thinner", None, registry, EventStream(), Budgets(), lambda: False,
                        None, None, [], None)

    assert INVENTORY in list(seen["args"]) + list(seen["kwargs"].values())


def test_design_modules_never_import_freecad():
    for path in (design_module.__file__, describe_module.__file__):
        tree = ast.parse(open(path).read())
        imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module}
        assert not imported & {"FreeCAD", "Part", "Import"}, path
