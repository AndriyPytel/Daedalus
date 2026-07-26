"""
Level 1 — the AST lint that runs before Preflight.

These are the two failures Preflight itself cannot catch: a literal element
name does not crash, it silently deforms the model; and Gui/selection code
runs differently in the worker than it will here, which makes Preflight
meaningless rather than wrong.
"""
import pytest

from core.lint import lint, selected_elements

SELECTED_EDGE_3 = [{"object": "Box", "elements": ["Edge3"]}]


# ── literal element names (ADR-0005) ──────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "fillet.Base = (box, ['Edge12'])",
    "fillet.Base = (box, ['Face3'])",
    "shape = box.Shape.getElement('Vertex7')",
    'ref = "Box.Edge12"',
])
def test_a_literal_element_name_is_rejected(code):
    result = lint(code)

    assert not result.ok
    assert "literal element name" in result.report()


def test_the_reason_tells_the_assistant_what_to_do_instead():
    result = lint("fillet.Base = (box, ['Edge12'])")

    reason = result.report()
    assert "Compute the reference at execution time" in reason
    assert "wrong edge" in reason


def test_a_computed_reference_is_accepted():
    code = (
        "box = doc.getObject('Box')\n"
        "top = max(box.Shape.Edges, key=lambda e: e.CenterOfMass.z)\n"
        "index = box.Shape.Edges.index(top) + 1\n"
        "fillet.Base = (box, ['Edge%d' % index])\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_name_that_merely_looks_like_one_is_not_an_element():
    assert lint("label = 'Edge'").ok
    assert lint("name = 'Edgecase12'").ok
    assert lint("obj.Label = 'Box12'").ok


def test_only_the_three_kinds_adr_0005_names_are_rejected():
    """ADR-0005 names Edge/Face/Vertex. 'Solid1' is a perfectly good Label."""
    assert lint("obj.Label = 'Solid1'").ok
    assert lint("name = 'Wire2'").ok
    assert lint("name = 'Shell3'").ok
    assert not lint("name = 'Edge1'").ok


# ── the user's live selection is explicit input ───────────────────────────────


def test_an_element_the_user_selected_is_accepted():
    result = lint("fillet.Base = (box, ['Edge3'])", selection=SELECTED_EDGE_3)

    assert result.ok, result.report()


def test_a_qualified_element_the_user_selected_is_accepted():
    result = lint("ref = 'Box.Edge3'", selection=SELECTED_EDGE_3)

    assert result.ok, result.report()


def test_a_different_element_is_still_rejected_when_something_is_selected():
    result = lint("fillet.Base = (box, ['Edge9'])", selection=SELECTED_EDGE_3)

    assert not result.ok


def test_selected_elements_flattens_a_snapshot_selection():
    assert selected_elements(SELECTED_EDGE_3) == {"Edge3", "Box.Edge3"}
    assert selected_elements(None) == set()


# ── Gui and live selection (ADR-0002) ─────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "import FreeCADGui",
    "import Gui",
    "from FreeCADGui import Selection",
    "Gui.ActiveDocument.ActiveView.viewAxometric()",
    "sel = FreeCADGui.Selection.getSelectionEx()",
    # Reaching the GUI through something else is the same problem by a longer route.
    "sel = App.Gui.Selection.getSelection()",
    "from FreeCAD import Gui",
    "from FreeCAD import Gui as g",
    "App.FreeCADGui.updateGui()",
])
def test_gui_and_live_selection_are_rejected(code):
    result = lint(code)

    assert not result.ok
    assert "not available during Preflight" in result.report()


def test_the_reason_explains_why_preflight_would_stop_meaning_anything():
    reason = lint("Gui.updateGui()").report()

    assert "pure function of the Document" in reason
    assert "headless" in reason


def test_document_only_code_passes():
    code = (
        "box = doc.addObject('Part::Box', 'Box')\n"
        "box.Length = 10\n"
        "doc.recompute()\n"
    )

    assert lint(code).ok


# ── shape of the result ───────────────────────────────────────────────────────


def test_code_that_does_not_parse_is_rejected_with_the_line():
    result = lint("box = doc.addObject(\n")

    assert not result.ok
    assert "does not parse" in result.problems[0]


def test_every_distinct_problem_is_reported_once():
    code = (
        "a = 'Edge12'\n"
        "b = 'Edge12'\n"
        "Gui.updateGui()\n"
        "Gui.updateGui()\n"
    )

    assert len(lint(code).problems) == 2


def test_both_kinds_of_problem_are_reported_together():
    result = lint("Gui.Selection.getSelection()\nfillet.Base = (box, ['Edge12'])")

    assert len(result.problems) == 2


def test_a_passing_result_is_truthy_and_reports_nothing():
    result = lint("doc.recompute()")

    assert result and result.ok
    assert result.report() == ""
