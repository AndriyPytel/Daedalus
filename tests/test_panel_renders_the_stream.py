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

from core.events import COST, DESIGN, KINDS

WRITTEN_NOT_SHOWN = {COST, DESIGN}

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


def test_every_event_kind_is_shown():
    """
    A kind added to the stream and forgotten is an event the user never sees.

    It is shown one of two ways: narrated, which is now plain Python and so is
    checked by running it, or rendered structurally by the panel because it is
    not a sentence — which is still a switch, and still parsed.

    `WRITTEN_NOT_SHOWN` is the third way and has two members, each written
    somewhere a reader can go and shown by nobody: what a call cost is a number
    for the session log rather than a sentence for the transcript, and the
    description is a document of its own, said by the path the `part` event
    carries. Naming them here is what keeps the rule for every other kind.
    """
    from core.narrate import Narration, narrate

    from tests.test_narrate import an_event

    structural = {
        node.attr for node in ast.walk(function(panel_tree(), "on_event"))
        if isinstance(node, ast.Attribute) and node.attr.isupper()
    }
    narrated = {kind.upper() for kind in KINDS
                if narrate(an_event(kind)) != Narration()}

    missing = ({kind.upper() for kind in KINDS} - structural - narrated
               - {kind.upper() for kind in WRITTEN_NOT_SHOWN})
    assert not missing, "neither narrated nor rendered: %s" % ", ".join(sorted(missing))


def test_the_panel_only_ever_asks_the_loop_to_start():
    """
    Rendering means reacting to events. The panel may name the loop's
    vocabulary — Approval and its three decisions are the Plan Gate's answer
    type, and the four answers about a failed result are the same kind of
    thing — but the only loop *function* it may call is `run`. Calling anything
    else would mean the event stream had stopped being the contract.

    Data types are fine to name; only `run` may be invoked.
    """
    tree = panel_tree()

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "core.loop":
            imported.update(alias.name for alias in node.names)

    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    vocabulary = {"Approval", "APPROVE", "REJECT", "AMEND",
                  "ACCEPT", "REVISE", "START_OVER", "DECISIONS"}
    assert imported <= vocabulary | {"run"}, \
        "the panel reaches into the loop for: %s" % (imported - vocabulary - {"run"})
    assert called & imported <= {"run", "Approval"}, \
        "the panel drives the loop by calling: %s" % (called & imported - {"run", "Approval"})


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


def test_the_dock_names_itself():
    """
    QMainWindow::saveState() warns about and then drops an unnamed dock, so
    without an objectName the panel's position never survives a restart.
    """
    panel = next(node for node in ast.walk(panel_tree())
                 if isinstance(node, ast.ClassDef) and node.name == "CopilotPanel")
    init = function(panel, "__init__")
    named = [
        node for node in ast.walk(init)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setObjectName"
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
    ]

    assert named, "CopilotPanel.__init__ never calls self.setObjectName()"


def test_the_run_is_stoppable():
    """
    The panel provides a stop mechanism that tells the loop by name, not by
    killing the thread.
    """
    tree = panel_tree()
    names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    assert {"on_stop", "stop"} <= names
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
