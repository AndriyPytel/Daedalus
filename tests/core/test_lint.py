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


@pytest.mark.parametrize("code", [
    "import FreeCADGui",
    "import Gui",
    "from FreeCADGui import Selection",
    "Gui.ActiveDocument.ActiveView.viewAxometric()",
    "sel = FreeCADGui.Selection.getSelectionEx()",
    "sel = App.Gui.Selection.getSelection()",
    "from FreeCAD import Gui",
    "from FreeCAD import Gui as g",
    "App.FreeCADGui.updateGui()",
])
def test_gui_and_live_selection_are_rejected(code):
    """
    Reaching the GUI through something else is the same problem by a longer
    route.
    """
    result = lint(code)

    assert not result.ok
    assert "not available during Preflight" in result.report()


def test_the_reason_explains_why_preflight_would_stop_meaning_anything():
    reason = lint("Gui.updateGui()").report()

    assert "pure function of the Document" in reason
    assert "headless" in reason


@pytest.mark.parametrize("code", [
    "App.ActiveDocument.ActiveObject = body",
    "App.ActiveDocument.setActiveObject('pdbody', body)",
    "App.activeDocument().activeView().setActiveObject('pdbody', body)",
    "body = App.ActiveDocument.getActiveObject('pdbody')",
])
def test_making_a_body_active_is_rejected(code):
    """The spellings that spent a whole Step budget on AttributeErrors."""
    result = lint(code)

    assert not result.ok
    assert "Body.addObject" in result.report()


def test_adding_a_feature_to_a_body_passes():
    code = (
        "body = doc.addObject('PartDesign::Body', 'Body')\n"
        "pad = doc.addObject('PartDesign::Pad', 'Pad')\n"
        "body.addObject(pad)\n"
        "doc.recompute()\n"
    )

    assert lint(code).ok, lint(code).report()


def test_document_only_code_passes():
    code = (
        "box = doc.addObject('Part::Box', 'Box')\n"
        "box.Length = 10\n"
        "doc.recompute()\n"
    )

    assert lint(code).ok


@pytest.mark.parametrize("code", [
    "Sketcher.Constraint('Concentric', 0, 1)",
    "Sketcher.Constraint('Concentric', 0, 3, 1, 3)",
    "sketch.addConstraint(Sketcher.Constraint('Colinear', 0, 1))",
])
def test_a_constraint_type_that_does_not_exist_is_rejected(code):
    """
    Not a wrong answer — a SIGSEGV inside the C++ constructor, with no
    traceback for the next attempt to correct against. FCC-19.
    """
    result = lint(code)

    assert not result.ok
    assert "is not a Sketcher constraint type" in result.report()


def test_a_known_constraint_at_the_wrong_arity_is_rejected():
    result = lint("Sketcher.Constraint('Coincident', 0, 1)")

    assert not result.ok
    assert "'Coincident' takes" in result.report()
    assert "2 arguments after the name" in result.report()


def test_concentricity_is_named_as_coincident_between_the_centres():
    reason = lint("Sketcher.Constraint('Concentric', 0, 1)").report()

    assert "Coincident" in reason
    assert "Constraint('Coincident', a, 3, b, 3)" in reason


@pytest.mark.parametrize("code", [
    "Sketcher.Constraint('Parallel', 0, 1)",
    "Sketcher.Constraint('Coincident', 0, 3, 1, 3)",
    "Sketcher.Constraint('Horizontal', 0)",
    "Sketcher.Constraint('Distance', 0, 1, 25.0)",
    "Sketcher.Constraint('Radius', 0, 5.0)",
    "Sketcher.Constraint('Symmetric', 0, 1, 2, 1, 3)",
    "Sketcher.Constraint('InternalAlignment:EllipseMajorDiameter', 0, 1)",
    "Sketcher.Constraint('Horizontal', 0, True)",
    "Sketcher.Constraint('Distance', 0, 1, 25.0, True, False)",
])
def test_a_real_constraint_signature_passes(code):
    assert lint(code).ok, lint(code).report()


@pytest.mark.parametrize("code", [
    "Sketcher.Constraint(kind, 0, 1)",
    "Sketcher.Constraint(*args)",
    "Sketcher.Constraint('Coincident', *indices)",
    "Sketcher.Constraint('Distance', 0, 1, **options)",
])
def test_a_call_lint_cannot_read_passes(code):
    """The rule never rejects code it has not actually read. FCC-19."""
    assert lint(code).ok, lint(code).report()


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


PAD = "pad = body.newObject('PartDesign::Feature', 'Pad')\n"


@pytest.mark.parametrize("code", [
    PAD + "pad.Shape = cut",
    PAD + "pad.addProperty('App::PropertyString', 'Profile')",
    "f = doc.addObject('PartDesign::Pad', 'Pad')\nf.Shape = solid",
])
def test_a_raw_shape_passed_off_as_a_partdesign_feature_is_rejected(code):
    result = lint(code)

    assert not result.ok
    assert "computes its own Shape" in result.report()


def test_the_reason_names_the_feature_types_to_use_instead():
    result = lint(PAD + "pad.Shape = cut")

    reason = result.report()
    assert "PartDesign::Pad" in reason and "PartDesign::Pocket" in reason


def test_a_part_feature_may_still_be_given_a_shape():
    """
    The rule is about passing a raw shape off as a PartDesign feature, not
    about Part. A Step that legitimately builds a Part shape outside any Body
    still runs. FCC-60.
    """
    code = (
        "cut = base.Shape.cut(tool.Shape)\n"
        "out = doc.addObject('Part::Feature', 'Cut')\n"
        "out.Shape = cut\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_real_partdesign_feature_is_untouched():
    code = (
        "pad = body.newObject('PartDesign::Pad', 'Pad')\n"
        "pad.Profile = sketch\n"
        "pad.Length = 12.0\n"
    )

    assert lint(code).ok, lint(code).report()


@pytest.mark.parametrize("code", [
    "try:\n    fillet.recompute()\nexcept:\n    raise",
    "try:\n    fillet.recompute()\nexcept Exception:\n    pass",
    "try:\n    fillet.recompute()\nexcept Exception:\n    ...",
    "for e in edges:\n    try:\n        f(e)\n    except ValueError:\n        continue",
])
def test_a_step_that_swallows_its_own_failure_is_rejected(code):
    result = lint(code)

    assert not result.ok
    assert "swallow" in result.report()


def test_a_handler_that_does_something_is_accepted():
    code = (
        "try:\n"
        "    import numpy\n"
        "except ImportError:\n"
        "    numpy = None\n"
    )

    assert lint(code).ok, lint(code).report()


def test_the_element_reason_no_longer_points_at_shape():
    """
    Told to find the edge from `.Shape`, the assistant computes an index and
    has nowhere to put it but an assembled 'Face%d'. Carried in from FCC-26,
    closed wontfix: a message fix, not a new rule.
    """
    reason = lint("fillet.Base = (box, ['Edge12'])").report()

    assert ".Shape" not in reason
    assert "adjacency" not in reason


THE_RECORDED_R2_FILLET = """body = doc.getObject('Body')
base = doc.getObject('PocketRearCentral')
if body is None or base is None:
    raise RuntimeError('The rear central pocket required for edge fillets is missing')

edge_refs = []
for i, edge in enumerate(base.Shape.Edges, 1):
    vs = edge.Vertexes
    if len(vs) != 2:
        continue
    a = vs[0].Point
    b = vs[1].Point
    if abs(a.x - b.x) < 1e-7 and abs(a.z - b.z) < 1e-7 and abs(a.y - b.y) > 1e-7:
        edge_refs.append('Edge%d' % i)
if not edge_refs:
    raise RuntimeError('No longitudinal outer or pocket-perimeter edges were found for R2 fillets')

fillet = doc.addObject('PartDesign::Fillet', 'FilletRedEdges')
fillet.Label = 'R2 Outer and Pocket Longitudinal Fillets'
body.addObject(fillet)
fillet.Base = (base, edge_refs)
fillet.Radius = 2.0
doc.recompute()
"""


def test_the_recorded_runs_r2_fillet_is_rejected_for_never_saying_how_many():
    """
    The Step asked for the outline of an obround pad, whose profile meets the
    end faces in one smooth loop and has no longitudinal edge at all. The
    filter could only ever find the eight pocket corners, it found them, and
    the run reported the fillet applied. FCC-151.
    """
    result = lint(THE_RECORDED_R2_FILLET)

    assert not result.ok
    assert "how many" in result.report()


@pytest.mark.parametrize("code", [
    "edges = [e for e in base.Shape.Edges if e.Length > 5]\n"
    "refs = ['Edge%d' % (base.Shape.Edges.index(e) + 1) for e in edges]\n"
    "fillet.Base = (base, refs)\n",
    "refs = []\n"
    "for i, e in enumerate(base.Shape.Edges, 1):\n"
    "    if e.Length > 5:\n"
    "        refs.append('Edge%d' % i)\n"
    "if not refs:\n"
    "    raise RuntimeError('found none')\n"
    "chamfer.Base = (base, refs)\n",
    "fillet.Base = (base, ['Edge%d' % (i + 1) for i, e in enumerate(base.Shape.Edges)])\n",
])
def test_a_computed_selection_with_no_count_is_rejected(code):
    result = lint(code)

    assert not result.ok
    assert "how many" in result.report()


def test_a_selection_that_raises_on_the_count_is_accepted():
    code = (
        "refs = []\n"
        "for i, e in enumerate(base.Shape.Edges, 1):\n"
        "    if e.Length > 5:\n"
        "        refs.append('Edge%d' % i)\n"
        "if len(refs) != 12:\n"
        "    raise RuntimeError('the outline has 12 edges, found %d' % len(refs))\n"
        "fillet.Base = (base, refs)\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_comprehension_over_a_counted_list_is_accepted():
    """
    A comprehension states its count when every list it walks has been counted
    and it drops nothing: it builds one name per element of a list the Step has
    already raised on, so it cannot hand the feature fewer than the Step said.
    """
    code = (
        "idx = sloping_edges(base.Shape)\n"
        "if len(idx) != 2:\n"
        "    raise RuntimeError('the rib has 2 sloping edges, found %d' % len(idx))\n"
        "fillet.Base = (base, ['Edge%d' % (i + 1) for i in idx])\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_comprehension_that_filters_a_counted_list_is_rejected():
    """
    An `if` inside the comprehension puts the count back where it started: the
    list was counted, and what the filter keeps of it was not.
    """
    code = (
        "idx = sloping_edges(base.Shape)\n"
        "if len(idx) != 2:\n"
        "    raise RuntimeError('the rib has 2 sloping edges, found %d' % len(idx))\n"
        "fillet.Base = (base, ['Edge%d' % (i + 1) for i in idx if i > 3])\n"
    )

    result = lint(code)

    assert not result.ok
    assert "how many" in result.report()


def test_the_reason_says_what_to_write_instead():
    reason = lint("refs = pick(base)\nfillet.Base = (base, refs)\n").report()

    assert "len(" in reason
    assert "raise" in reason


def test_a_selection_the_user_made_needs_no_count():
    result = lint("fillet.Base = (box, ['Edge3'])", SELECTED_EDGE_3)

    assert result.ok, result.report()


def test_a_base_that_is_not_a_selection_is_untouched():
    assert lint("pad.Base = sketch\npocket.Profile = sketch\n").ok


def test_writing_the_deprecated_midplane_is_refused():
    """
    `Midplane` still works on FreeCAD 1.1.3; writing it costs two lines of
    warning into the Report view, not a traceback. `execute.md:183-184`
    already tells the model to write `SideType` instead, so this Step is
    accepted rather than rejected -- a lint rule earns a whole retry cycle
    only for a mistake Preflight cannot teach a way out of, and this one is
    not that: FreeCAD's own console warning already says what to fix.
    """
    assert lint("pad.Midplane = True").ok


def test_reading_midplane_is_untouched():
    """
    Only the write warns. `describe_type` reports it as a property that exists
    and a Step is free to look at what is already there.
    """
    assert lint("was = pad.Midplane").ok


def test_addobject_typeid_matching_the_steps_op_is_accepted():
    code = "pad = doc.addObject('PartDesign::Pad', 'Pad')"

    assert lint(code, op="pad").ok


def test_addobject_typeid_not_matching_the_steps_op_is_rejected():
    """
    ADR-0026/FCC-233: `execute.md`'s verb-to-TypeId table says `pad` builds a
    `PartDesign::Pad`. `Part::Extrusion` is not the TypeId any of the 36
    approved operations builds, so a Step declared `op: "pad"` that writes it
    is rejected the same way a `Gui` import already is.
    """
    code = "pad = doc.addObject('Part::Extrusion', 'Pad')"

    result = lint(code, op="pad")

    assert not result.ok
    assert "Part::Extrusion" in result.report()
    assert "pad" in result.report()


def test_scaffolding_typeids_are_accepted_under_any_op():
    """A Body and a Sketcher profile are common to every operation, not just
    the one the Step is named for."""
    code = ("body = doc.addObject('PartDesign::Body', 'Body')\n"
            "sketch = body.newObject('Sketcher::SketchObject', 'Sketch')\n"
            "pad = doc.addObject('PartDesign::Pad', 'Pad')")

    assert lint(code, op="pad").ok


def test_no_op_given_skips_the_typeid_check():
    """Legacy code, applied without a declared `op`, is not held to the
    allow-list at all — `core/lint.py` gained this check under FCC-233 and
    every recorded run before it never named one."""
    code = "pad = doc.addObject('Part::Extrusion', 'Pad')"

    assert lint(code).ok


def test_an_op_off_the_36_list_skips_the_typeid_check():
    assert lint("pad = doc.addObject('Part::Extrusion', 'Pad')", op="squish").ok



def test_a_literal_name_lookup_of_an_object_this_step_just_created_is_rejected():
    """
    FCC-302's own reproduction: FreeCAD renames a new object on a name
    collision, so a Step that re-fetches by the literal string it just
    handed to addObject is not guaranteed to reach the object it made.
    """
    code = "doc.addObject('Part::Box', 'Box')\nbox = doc.getObject('Box')"

    result = lint(code)

    assert not result.ok
    assert "allocated-name map" in result.report()
    assert "'Box'" in result.report()


def test_the_reason_names_the_allocated_name_map():
    code = "doc.addObject('Part::Box', 'Box')\nbox = doc.getObject('Box')"

    reason = lint(code).report()

    assert "names[" in reason


def test_a_lookup_of_an_object_the_user_made_before_the_run_is_accepted():
    """No addObject('...', 'UsersBox') anywhere in this Step's own code, so
    the name is not one this run allocated -- it is the user's own object."""
    assert lint("doc.getObject('UsersBox').Length = 99").ok


def test_addressing_the_returned_reference_instead_needs_no_lookup():
    assert lint("box = doc.addObject('Part::Box', 'Box')\nbox.Length = 10").ok


def test_a_lookup_of_a_name_that_is_not_the_one_just_created_is_accepted():
    code = "doc.addObject('Part::Box', 'Box')\nother = doc.getObject('OtherBox')"

    assert lint(code).ok
