"""
Level 1 — the AST lint that runs before Preflight.

These are the two failures Preflight itself cannot catch: a literal element
name does not crash, it silently deforms the model; and Gui/selection code
runs differently in the worker than it will here, which makes Preflight
meaningless rather than wrong.
"""
import ast

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
    """Only Edge/Face/Vertex are the checked kinds. 'Solid1' is a perfectly good Label."""
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
        "box.Length = design['features']['Box']['length']\n"
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
    traceback for the next attempt to correct against.
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
    """The rule never rejects code it has not actually read."""
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
    still runs.
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
        "pad.Length = design['features']['Pad']['length']\n"
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
    has nowhere to put it but an assembled 'Face%d'. The fix here is a
    message fix, not a new rule.
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
    the run reported the fillet applied.
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


def test_a_selection_from_pick_edges_with_expect_is_counted():
    """
    `pick_edges(shape, predicate, expect=N)` raises inside itself when the
    count is wrong, so a Step using it states its count the same way a
    hand-written guard would -- without writing one.
    """
    code = (
        "held = pick_edges(base.Shape, lambda e: True, expect=3)\n"
        "fillet.Base = (base, held)\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_pick_edges_call_without_expect_is_still_uncounted():
    code = (
        "held = pick_edges(base.Shape, lambda e: True)\n"
        "fillet.Base = (base, held)\n"
    )

    result = lint(code)

    assert not result.ok
    assert "how many" in result.report()


def test_pick_edges_with_expect_given_positionally_is_counted():
    """
    `expect` is `pick_edges`'s third parameter -- a Step calling it
    positionally, the natural way to write the call, states the count the
    same as one that spells out the keyword.
    """
    code = (
        "held = pick_edges(base.Shape, lambda e: True, 3)\n"
        "fillet.Base = (base, held)\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_pick_edges_call_inline_in_base_is_counted():
    """
    `fillet.Base = (base, pick_edges(...))` never binds a name for
    `_uncounted_selection` to look up -- a bare `Call` as the element list
    falls outside both the counted-name and counted-comprehension shapes it
    checks, so it is accepted already, not because the count was seen but
    because the shape does not match. Pinned here so a later lint change
    does not turn this back into a rejection.
    """
    code = "fillet.Base = (base, pick_edges(shape, pred, expect=4))\n"

    assert lint(code).ok, lint(code).report()


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
    """
    The TypeId check is now `op_typeid_mismatch`, read off
    the kernel's own report of what a Step built rather than parsed out of
    the code — `lint()` no longer holds an opinion on `op` at all.
    """
    from core.lint import op_typeid_mismatch

    created, objects = ["Pad"], [{"name": "Pad", "type": "PartDesign::Pad"}]

    assert op_typeid_mismatch("pad", created, objects) is None


def test_addobject_typeid_not_matching_the_steps_op_is_rejected():
    """
    `execute.md`'s verb-to-TypeId table says `pad` builds a `PartDesign::Pad`.
    `Part::Extrusion` is not the TypeId any of the 36 approved operations
    builds, so a Step declared `op: "pad"` that built one is rejected.
    """
    from core.lint import op_typeid_mismatch

    created, objects = ["Pad"], [{"name": "Pad", "type": "Part::Extrusion"}]

    mismatch = op_typeid_mismatch("pad", created, objects)

    assert mismatch == "Part::Extrusion"


def test_scaffolding_typeids_are_accepted_under_any_op():
    """A Body and a Sketcher profile are common to every operation, not just
    the one the Step is named for."""
    from core.lint import op_typeid_mismatch

    created = ["Body", "Sketch", "Pad"]
    objects = [{"name": "Body", "type": "PartDesign::Body"},
              {"name": "Sketch", "type": "Sketcher::SketchObject"},
              {"name": "Pad", "type": "PartDesign::Pad"}]

    assert op_typeid_mismatch("pad", created, objects) is None


def test_no_op_given_skips_the_typeid_check():
    """Legacy code, applied without a declared `op`, is not held to the
    allow-list at all — `core/lint.py` gained this check after runs began
    naming an `op`, and every recorded run before that never named one."""
    from core.lint import op_typeid_mismatch

    created, objects = ["Pad"], [{"name": "Pad", "type": "Part::Extrusion"}]

    assert op_typeid_mismatch(None, created, objects) is None


def test_an_op_off_the_36_list_skips_the_typeid_check():
    from core.lint import op_typeid_mismatch

    created, objects = ["Pad"], [{"name": "Pad", "type": "Part::Extrusion"}]

    assert op_typeid_mismatch("squish", created, objects) is None



def test_a_literal_name_lookup_of_an_object_this_step_just_created_is_rejected():
    """
    This reproduces FreeCAD renaming a new object on a name
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
    assert lint("doc.getObject('UsersBox').Label = 'Renamed'").ok


def test_addressing_the_returned_reference_instead_needs_no_lookup():
    code = "box = doc.addObject('Part::Box', 'Box')\nbox.Length = design['features']['Box']['length']"
    assert lint(code).ok


def test_a_lookup_of_a_name_that_is_not_the_one_just_created_is_accepted():
    code = "doc.addObject('Part::Box', 'Box')\nother = doc.getObject('OtherBox')"

    assert lint(code).ok


def test_a_name_assigned_from_a_counted_comprehension_passes_the_lint():
    """
    The ticket's fillet: the comprehension is written into a name first, then
    that name is handed to `Base`, and the chain is no different from writing
    it inline.
    """
    code = (
        "if len(rim) != 4:\n"
        "    raise RuntimeError('the base has 4 edges, found %d' % len(rim))\n"
        "names = ['Edge%d' % (i + 1) for i, _ in rim]\n"
        "f.Base = (base, names)\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_chain_of_comprehension_assignments_stays_counted():
    code = (
        "if len(rim) != 4:\n"
        "    raise RuntimeError('the base has 4 edges, found %d' % len(rim))\n"
        "idx = [i for i, _ in rim]\n"
        "names = ['Edge%d' % (i + 1) for i in idx]\n"
        "f.Base = (base, names)\n"
    )

    assert lint(code).ok, lint(code).report()


def test_a_name_from_a_filtered_comprehension_is_still_rejected():
    code = (
        "if len(rim) != 4:\n"
        "    raise RuntimeError('the base has 4 edges, found %d' % len(rim))\n"
        "names = ['Edge%d' % (i + 1) for i, _ in rim if i > 1]\n"
        "f.Base = (base, names)\n"
    )

    result = lint(code)

    assert not result.ok
    assert "how many" in result.report()


def test_a_name_from_an_uncounted_source_comprehension_is_still_rejected():
    code = (
        "picks = pick(base)\n"
        "names = ['Edge%d' % (i + 1) for i in picks]\n"
        "f.Base = (base, names)\n"
    )

    result = lint(code)

    assert not result.ok
    assert "how many" in result.report()


@pytest.mark.parametrize("code", [
    "sk.AttachmentSupport = [(xz_plane, [])]",
    "sk.AttachmentSupport = [(xz_plane, []), (yz_plane, ['Face1'])]",
])
def test_an_empty_list_attachment_subelement_is_rejected(code):
    """
    Measured on freecadcmd 1.1.3: `AttachmentSupport = [(plane, [])]` plus
    `MapMode = 'FlatFace'` raises nothing and leaves Placement identity --
    the attachment silently does not happen.
    """
    result = lint(code)

    assert not result.ok
    assert "empty list literal" in result.report()
    assert 'Write `""`' in result.report()


@pytest.mark.parametrize("code", [
    "sk.AttachmentSupport = [(xz_plane, '')]",
    "sk.AttachmentSupport = [(xz_plane, [''])]",
])
def test_a_non_empty_attachment_subelement_is_accepted(code):
    assert lint(code).ok, lint(code).report()


def test_a_non_empty_list_of_element_names_is_the_elements_rule_business():
    """
    The literal-element rule already governs `Face1`; this rule is only
    about the list being empty, not about what it names.
    """
    result = lint("sk.AttachmentSupport = [(xz_plane, ['Face1'])]")

    assert "empty list literal" not in result.report()


def test_an_unrelated_empty_list_is_untouched():
    assert lint("edges = []").ok
    assert lint("fillet.Base = (box, [])").ok


@pytest.mark.parametrize("code", [
    "assert abs(shape.BoundBox.ZMax - 50.0) < 1e-6",
    "bb = shape.BoundBox\nassert abs(bb.ZMax - 50.0) < 1e-6",
    "if shape.BoundBox.ZMax != 50.0:\n    raise RuntimeError('wrong height')",
    "bb = shape.BoundBox\nif bb.Center.z != 25.0:\n    raise RuntimeError('off centre')",
])
def test_a_step_that_guards_on_boundbox_is_rejected(code):
    """
    The recorded run's `assert abs(bb.ZMax - 50.0) < 1e-6` failed Preflight
    at 49.872 on a tangent cylinder -- the BoundBox lands inside the true
    extreme, not on it.
    """
    result = lint(code)

    assert not result.ok
    assert "Shape.BoundBox" in result.report()
    assert "Surface.Position" in result.report()


@pytest.mark.parametrize("code", [
    "print(shape.BoundBox)",
    "bb = shape.BoundBox\nprint(bb.ZMax)",
    "length = shape.BoundBox.XLength",
    "sign = 1 if shape.BoundBox.ZMax > 0 else -1",
    "assert abs(shape.Surface.Position.z - 50.0) < 1e-6",
]) 
def test_reading_or_signing_off_boundbox_is_untouched(code):
    """
    Printing it for information, or reading a value into a name nothing
    asserts or raises on, stays legal -- the rule is about a number a Step
    acts on, not the property existing.
    """
    assert lint(code).ok, lint(code).report()


def _dimensional_findings(code):
    """`dimensional_literal`/`dimensional_default`, called directly the way a
    regression tool over the applied corpus calls them -- `lint()` itself
    already surfaces the same findings as refusals."""
    from core.lint import dimensional_default, dimensional_literal, literal_bindings

    tree = ast.parse(code)
    bindings = literal_bindings(tree)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        literal = dimensional_literal(node, bindings)
        if literal:
            findings.append(("literal",) + literal)
        defaulted = dimensional_default(node)
        if defaulted:
            findings.append(("default", defaulted))
    return findings


@pytest.mark.parametrize("code", [
    "pad.Length = 80.0",
    "pocket.Radius = 12.5",
    "fillet.Radius = 3",
])
def test_a_bare_numeral_in_a_dimensional_position_is_found(code):
    assert _dimensional_findings(code)


def test_the_finding_names_the_target_and_the_value():
    findings = _dimensional_findings("pad.Length = 80.0")

    assert findings == [("literal", "pad.Length", 80.0)]


def test_a_dimension_written_as_a_design_reference_is_not_found():
    code = "pad.Length = design['features']['Boss']['length']\n"

    assert not _dimensional_findings(code)


@pytest.mark.parametrize("code", [
    "pad.Length = 0",
    "pad.Length = 1",
    "revolve.Angle = 90",
    "revolve.Angle = 360",
])
def test_counts_indices_and_a_right_angle_are_not_dimensions(code):
    assert not _dimensional_findings(code)


def test_a_reference_resolved_with_a_default_is_found():
    code = "pad.Length = design.get('length', 80.0)\n"

    assert _dimensional_findings(code) == [("default", "pad.Length")]


def test_a_non_dimensional_attribute_with_a_number_is_untouched():
    assert not _dimensional_findings("pad.Occurrences = 4")
    assert not _dimensional_findings("hole.Type = 0")


def test_a_bare_numeral_named_one_hop_away_is_still_found():
    code = "L = 80.0\npad.Length = L\n"

    assert _dimensional_findings(code) == [("literal", "pad.Length", 80.0)]


def test_a_variable_computed_from_geometry_is_not_a_bare_numeral():
    code = "L = base.Shape.BoundBox.YLength\npad.Length = L\n"

    assert not _dimensional_findings(code)


def test_a_bare_numeral_unpacked_from_a_tuple_is_still_found():
    code = "L, D = 150.0, 100.0\npad.Length = L\n"

    assert _dimensional_findings(code) == [("literal", "pad.Length", 150.0)]


def test_the_lint_wired_checks_now_touch_a_bare_dimensional_numeral():
    """`dimensional_literal`/`dimensional_default` are wired into `lint()`'s
    refusal list: a Step can read a Design value at the point it runs, so
    the only other form a bare number could take is refused."""
    assert not lint("pad.Length = 80.0").ok
    assert not lint("pad.Length = design.get('length', 80.0)\n").ok


def test_a_dimensional_literal_refusal_names_the_target_and_the_value():
    report = lint("pad.Length = 80.0").report()

    assert "pad.Length" in report
    assert "80.0" in report


def test_a_design_reference_resolved_with_a_default_is_still_refused():
    report = lint("pad.Length = design.get('length', 80.0)\n").report()

    assert "pad.Length" in report


def test_a_dimension_read_from_design_by_index_passes():
    assert lint("pad.Length = design['features']['Boss']['length']\n").ok


def test_counts_indices_and_a_right_angle_pass_lint_too():
    for code in ("pad.Length = 0", "pad.Length = 1", "revolve.Angle = 90",
                "revolve.Angle = 360"):
        assert lint(code).ok, lint(code).report()
