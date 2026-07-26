"""
"The panel renders the event stream and does not call loop methods" is an
architectural claim, so it is checked by parsing the panel rather than by
reading it and hoping.

The Qt behaviour itself is Level 3 — a written manual script, not an automated
test (#10). What is checked here is the wiring: every event kind is rendered,
and the only thing the panel asks of the loop is to start.
"""
import ast
import os

from core.events import KINDS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "ui", "copilot_panel.py")


def panel_tree():
    with open(PANEL, encoding="utf-8") as handle:
        return ast.parse(handle.read(), PANEL)


def function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s has no %s" % (PANEL, name))


def test_every_event_kind_is_rendered():
    """
    A kind added to the stream and forgotten in the panel is an event the user
    never sees. Caught here instead of in a demo.
    """
    rendered = {
        node.attr for node in ast.walk(function(panel_tree(), "on_event"))
        if isinstance(node, ast.Attribute) and node.attr.isupper()
    }
    expected = {kind.upper() for kind in KINDS}

    assert expected <= rendered, "not rendered: %s" % ", ".join(sorted(expected - rendered))


def test_the_panel_only_ever_asks_the_loop_to_start():
    """
    Rendering means reacting to events. If the panel reached into the loop for
    anything else, the event stream would have stopped being the contract.
    """
    tree = panel_tree()
    from_loop = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.loop":
            from_loop.update(alias.name for alias in node.names)

    assert from_loop == {"run"}, "the panel reaches into the loop for: %s" % from_loop


def test_the_panel_never_touches_the_document_or_the_worker_directly():
    tree = panel_tree()
    forbidden = ("ActiveDocument", "openTransaction", "commitTransaction",
                 "PreflightWorker", "check")
    found = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }

    assert not found, "the panel does its own Document work: %s" % found


def test_the_loop_runs_off_the_gui_thread():
    """User story 10: FreeCAD must stay usable while the assistant thinks."""
    tree = panel_tree()
    calls = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "moveToThread" in calls
    assert "run" in {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_the_run_is_stoppable():
    tree = panel_tree()
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    assert {"on_stop", "stop"} <= names
    # and the loop is told about it by name, not by killing the thread
    assert "cancelled" in {
        keyword.arg for node in ast.walk(tree)
        if isinstance(node, ast.Call) for keyword in node.keywords
    }


def test_the_stream_is_persisted():
    tree = panel_tree()
    calls = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "log_to" in calls, "the session log is never written"
