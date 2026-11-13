"""
"The panel renders the event stream" is an architectural claim, so it is
checked by parsing the panel rather than by reading it and hoping.

The Qt behaviour itself is Level 3 — a written manual script, not an automated
test (#10). What is checked here is the wiring: every kind the panel names is
one the stream registers.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
PANEL = os.path.join(ROOT, "Mod", "ui", "daedalus_panel.py")


def panel_tree():
    with open(PANEL, encoding="utf-8") as handle:
        return ast.parse(handle.read(), PANEL)


def function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("%s has no %s" % (PANEL, name))


def test_every_event_kind_is_shown():
    """
    A kind added to the stream and forgotten is an event the user never sees.

    The one table says how each is shown: narrated, or structural. Every kind
    `on_event` names in code must be a kind the table registers.
    """
    from core import events

    named = {
        node.attr for node in ast.walk(function(panel_tree(), "on_event"))
        if isinstance(node, ast.Attribute) and node.attr.isupper() and node.attr != "KINDS"
    }
    unregistered = {name for name in named if getattr(events, name) not in events.KINDS}

    assert not unregistered, "the panel names kinds the stream does not register: %s" % \
        ", ".join(sorted(unregistered))
    assert all(kind.narration or kind.structural for kind in events.REGISTRY.values())


def test_the_panel_never_touches_the_document_or_the_worker_directly():
    tree = panel_tree()
    forbidden = ("ActiveDocument", "openTransaction", "commitTransaction",
                 "Kernel", "check")
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
    assert "send" in calls, "LoopWorker.do_work must call sdk_client.send()"
