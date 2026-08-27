"""
FCC-238: the closed 36-`op` vocabulary (ADR-0026, FCC-233) must be complete
over the FreeCAD classes that build design geometry, not just closed. This
is the survey `core.lint.OP_TYPEIDS` is checked against.

**Method.** Every `PartDesign::`, `Part::` and `Sketcher::` type FreeCAD 1.1.3
itself offers — `doc.supportedTypes()` after `import Part, PartDesign,
Sketcher`, `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`,
134 types, 3 of which (`Part::Boolean`, `Part::Datum`, `Part::Primitive`)
`doc.addObject` itself refuses as abstract and are left out of the 134 —
was placed under the op that builds it if `wiki.freecad.org`'s current
PartDesign/Part/Sketcher Workbench tool navbox names it as a Tool, or
excluded with a reason otherwise. This file cannot import FreeCAD (not on
this repo's own `.venv`, only reachable via the system `freecadcmd`), so the
134-type inventory is frozen here as data, the same way
`tests/recorded/vocabulary_steps.json` freezes a `logs/` measurement this
suite cannot re-run live.

`SURVEYED_TYPEID_TO_OP` is the "owned" half: each key must appear in
`core.lint.OP_TYPEIDS[value]`. `EXCLUDED_TYPEIDS` is the other half: a
reason, not a copy of a verdict recorded nowhere. Together they cover all
134 surveyed types with no overlap — asserted below, over the *op list this
file reads out of `config/prompts/language.md`*, the same way
`test_op_vocabulary.py` does, rather than a copy of the 36 names.

**The one deliberate exception.** `PartDesign::Pocket` sits under two ops in
`OP_TYPEIDS` — `pocket`, its wiki.freecad.org Tool, and `hole` — not because
Pocket and Hole are the same Tool (they are not), but because session
afa0142baef847b0a9cd9139390af993 Step 5 cut a hole with a circular-sketch
`PartDesign::Pocket`, a normal and often more robust FreeCAD technique, and
was refused before Preflight for it, then retried onto `PartDesign::Hole`,
which failed at runtime with "No base set, no sketch support either"
(FCC-238). `SURVEYED_TYPEID_TO_OP` still places `PartDesign::Pocket` under
`pocket` alone — the survey's canonical Tool-to-op assignment is unchanged;
only `OP_TYPEIDS`'s lint-acceptance table widens, in this one named place,
by ticket-recorded exception rather than by a second table nothing else in
the codebase reads.
"""
import os
import re

from core.lint import OP_TYPEIDS, lint

LANGUAGE_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "prompts", "language.md")

SHARED_TYPEID_EXCEPTIONS = {
    "PartDesign::Pocket": ("pocket", "hole"),
    "PartDesign::Hole": ("hole", "thread"),
}
"""
Two facts about FreeCAD, not two bugs: a Hole feature builds a thread by
setting its own Threaded property rather than through a distinct TypeId
(language.md: "A thread is a hole whose prose gives the thread's own
parameters"), and cutting a hole with a circular-sketch Pocket is a normal
FreeCAD technique this ticket's defect (FCC-238) found banned. A third
exception should not be added here — at that point the closed vocabulary's
premise, that a TypeId names one operation, is false often enough that
injectivity is the wrong property to assert, and this test needs rewriting
rather than a longer list.
"""


def _language_md():
    with open(LANGUAGE_MD, encoding="utf-8") as f:
        return f.read()


def _op_vocabulary():
    text = _language_md()
    start = text.index("## The operation is one of 36")
    end = text.index("## A Step is one sentence")
    section = text[start:end]
    listed = re.search(r"one of \(([^)]+)\)", section)
    return [w.strip() for w in listed.group(1).split(",")]


SURVEYED_TYPEID_TO_OP = {
    'PartDesign::AdditiveBox': 'additive-primitive',
    'PartDesign::SubtractiveBox': 'subtractive-primitive',
    'PartDesign::AdditiveCone': 'additive-primitive',
    'PartDesign::SubtractiveCone': 'subtractive-primitive',
    'PartDesign::AdditiveCylinder': 'additive-primitive',
    'PartDesign::SubtractiveCylinder': 'subtractive-primitive',
    'PartDesign::AdditiveEllipsoid': 'additive-primitive',
    'PartDesign::SubtractiveEllipsoid': 'subtractive-primitive',
    'PartDesign::AdditivePrism': 'additive-primitive',
    'PartDesign::SubtractivePrism': 'subtractive-primitive',
    'PartDesign::AdditiveSphere': 'additive-primitive',
    'PartDesign::SubtractiveSphere': 'subtractive-primitive',
    'PartDesign::AdditiveTorus': 'additive-primitive',
    'PartDesign::SubtractiveTorus': 'subtractive-primitive',
    'PartDesign::AdditiveWedge': 'additive-primitive',
    'PartDesign::SubtractiveWedge': 'subtractive-primitive',
    'PartDesign::AdditiveHelix': 'additive-helix',
    'PartDesign::AdditiveLoft': 'additive-loft',
    'PartDesign::AdditivePipe': 'additive-sweep',
    'PartDesign::SubtractiveHelix': 'subtractive-helix',
    'PartDesign::SubtractiveLoft': 'subtractive-loft',
    'PartDesign::SubtractivePipe': 'subtractive-sweep',
    'PartDesign::Body': 'body',
    'PartDesign::Chamfer': 'chamfer',
    'PartDesign::CoordinateSystem': 'local-coordinate-system',
    'PartDesign::Draft': 'draft',
    'PartDesign::Fillet': 'fillet',
    'PartDesign::Groove': 'groove',
    'PartDesign::Hole': 'hole',
    'PartDesign::Line': 'datum-line',
    'PartDesign::LinearPattern': 'linear-pattern',
    'PartDesign::Mirrored': 'mirror',
    'PartDesign::MultiTransform': 'multi-transform',
    'PartDesign::Pad': 'pad',
    'PartDesign::Plane': 'datum-plane',
    'PartDesign::Pocket': 'pocket',
    'PartDesign::Point': 'datum-point',
    'PartDesign::PolarPattern': 'polar-pattern',
    'PartDesign::Revolution': 'revolve',
    'PartDesign::Scaled': 'scale',
    'PartDesign::ShapeBinder': 'sub-shape-binder',
    'PartDesign::SubShapeBinder': 'sub-shape-binder',
    'PartDesign::Thickness': 'thickness',
    'Part::Common': 'common',
    'Part::MultiCommon': 'common',
    'Part::Cut': 'cut',
    'Part::Fuse': 'fuse',
    'Part::MultiFuse': 'fuse',
    'Part::Offset': 'offset',
    'Part::Offset2D': 'offset',
    'Sketcher::SketchObject': 'sketch',
}


R_ABSTRACT = (
    "internal base class other PartDesign/Part feature types derive from; the "
    "current PartDesign/Part Workbench tool navbox (wiki.freecad.org) never "
    "names it as a Tool a user, or Execute, selects"
)

R_LEGACY_PRIMITIVE = (
    "pre-0.19 legacy PartDesign primitive, superseded by the "
    "Additive/Subtractive-prefixed family; the current PartDesign Workbench tool "
    "navbox (wiki.freecad.org/PartDesign_Workbench) lists only the "
    "Additive/Subtractive names, this bare one only opens documents saved before "
    "0.19"
)

R_OUTSIDE_BODY_DUP = (
    "Part Workbench tool building the same shape outside any Body, duplicating "
    "an existing in-Body PartDesign op; core/lint.py already bans "
    "Part::Extrusion in place of PartDesign::Pad for exactly this reason (its "
    "own docstring), and this app models one Body per part "
    "(config/prompts/design.md speaks throughout of 'the part')"
)

R_NON_SOLID = (
    "produces a face, wire, curve or point, not a solid Body feature — this "
    "app's sole profile source is Sketcher::SketchObject (core/lint.py's "
    "_RAW_SHAPE_REASON), not a raw Part-level 2D/point primitive"
)

R_IMPORT = (
    "imports geometry from an external BREP/IGES/STEP file rather than building "
    "it; 'Import CAD file...' is not even offered in the Part Workbench menu as "
    "of FreeCAD 1.1 (wiki.freecad.org/Part_Workbench, 'Obsolete tools')"
)

R_NON_GENERATIVE = (
    "post-processing tool applied to an existing shape (refine topology, reverse "
    "face orientation); it does not build new design geometry"
)

R_GROUPING = (
    "groups existing objects into a compound container without building new "
    "geometry"
)

R_SURFACE = (
    "Surface-workbench-adjacent tool producing an open surface, not a solid — "
    "out of scope per docs/research/ste-freecad-verbs.md §4"
)

R_SKETCH_INTERNAL = (
    "not produced by the Sketcher Workbench's one Tool, New Sketch "
    "(wiki.freecad.org/Sketcher_Workbench); *Python is the FeaturePython "
    "scripting base and *SF is the internal Sketcher Solver Framework variant, "
    "neither is a Tool a Step could be told to use"
)

R_BOOLEAN_AMBIGUOUS = (
    "its geometric operation — Union, Cut or Intersection — is picked by a "
    "`Type` property value, not by the TypeId, so no single-entry TypeId-keyed "
    "table can place it under exactly one op without a lint check keyed off that "
    "property; a Step wanting a body-level boolean already has fuse/cut/common "
    "via Part::MultiFuse/Fuse/Cut/Common/MultiCommon outside the Body "
    "(execute.md's documented Part::Feature carve-out), which has no such "
    "ambiguity"
)


EXCLUDED_TYPEIDS = {
    'Part::BodyBase': R_ABSTRACT,
    'Part::Box': R_OUTSIDE_BODY_DUP,
    'Part::Chamfer': R_OUTSIDE_BODY_DUP,
    'Part::Circle': R_NON_SOLID,
    'Part::Compound': R_GROUPING,
    'Part::Compound2': R_GROUPING,
    'Part::Cone': R_OUTSIDE_BODY_DUP,
    'Part::CurveNet': R_SURFACE,
    'Part::CustomFeature': R_ABSTRACT,
    'Part::CustomFeaturePython': R_ABSTRACT,
    'Part::Cylinder': R_OUTSIDE_BODY_DUP,
    'Part::DatumLine': R_OUTSIDE_BODY_DUP,
    'Part::DatumPlane': R_OUTSIDE_BODY_DUP,
    'Part::DatumPoint': R_OUTSIDE_BODY_DUP,
    'Part::Ellipse': R_NON_SOLID,
    'Part::Ellipsoid': R_OUTSIDE_BODY_DUP,
    'Part::Extrusion': R_OUTSIDE_BODY_DUP,
    'Part::Face': R_NON_SOLID,
    'Part::Feature': R_ABSTRACT,
    'Part::FeatureExt': R_ABSTRACT,
    'Part::FeatureGeometrySet': R_ABSTRACT,
    'Part::FeaturePython': R_ABSTRACT,
    'Part::Fillet': R_OUTSIDE_BODY_DUP,
    'Part::FilletBase': R_ABSTRACT,
    'Part::Helix': R_NON_SOLID,
    'Part::ImportBrep': R_IMPORT,
    'Part::ImportIges': R_IMPORT,
    'Part::ImportStep': R_IMPORT,
    'Part::Line': R_NON_SOLID,
    'Part::LocalCoordinateSystem': R_OUTSIDE_BODY_DUP,
    'Part::Loft': R_OUTSIDE_BODY_DUP,
    'Part::Mirroring': R_OUTSIDE_BODY_DUP,
    'Part::Part2DObject': R_ABSTRACT,
    'Part::Part2DObjectPython': R_ABSTRACT,
    'Part::Plane': R_NON_SOLID,
    'Part::Polygon': R_NON_SOLID,
    'Part::Prism': R_OUTSIDE_BODY_DUP,
    'Part::ProjectOnSurface': R_SURFACE,
    'Part::Refine': R_NON_GENERATIVE,
    'Part::RegularPolygon': R_NON_SOLID,
    'Part::Reverse': R_NON_GENERATIVE,
    'Part::Revolution': R_OUTSIDE_BODY_DUP,
    'Part::RuledSurface': R_SURFACE,
    'Part::Scale': R_OUTSIDE_BODY_DUP,
    'Part::Section': R_SURFACE,
    'Part::Sphere': R_OUTSIDE_BODY_DUP,
    'Part::Spiral': R_NON_SOLID,
    'Part::Spline': R_NON_SOLID,
    'Part::Sweep': R_OUTSIDE_BODY_DUP,
    'Part::Thickness': R_OUTSIDE_BODY_DUP,
    'Part::Torus': R_OUTSIDE_BODY_DUP,
    'Part::Vertex': R_NON_SOLID,
    'Part::Wedge': R_OUTSIDE_BODY_DUP,
    'PartDesign::Boolean': R_BOOLEAN_AMBIGUOUS,
    'PartDesign::Box': R_LEGACY_PRIMITIVE,
    'PartDesign::Cone': R_LEGACY_PRIMITIVE,
    'PartDesign::Cylinder': R_LEGACY_PRIMITIVE,
    'PartDesign::DressUp': R_ABSTRACT,
    'PartDesign::Ellipsoid': R_LEGACY_PRIMITIVE,
    'PartDesign::Feature': R_ABSTRACT,
    'PartDesign::FeatureAddSub': R_ABSTRACT,
    'PartDesign::FeatureAddSubPython': R_ABSTRACT,
    'PartDesign::FeatureAdditivePython': R_ABSTRACT,
    'PartDesign::FeatureBase': R_ABSTRACT,
    'PartDesign::FeatureExtrude': R_ABSTRACT,
    'PartDesign::FeaturePrimitive': R_ABSTRACT,
    'PartDesign::FeaturePython': R_ABSTRACT,
    'PartDesign::FeatureRefine': R_ABSTRACT,
    'PartDesign::FeatureRefinePython': R_ABSTRACT,
    'PartDesign::FeatureSubtractivePython': R_ABSTRACT,
    'PartDesign::Helix': R_ABSTRACT,
    'PartDesign::Loft': R_ABSTRACT,
    'PartDesign::Pipe': R_ABSTRACT,
    'PartDesign::Prism': R_LEGACY_PRIMITIVE,
    'PartDesign::ProfileBased': R_ABSTRACT,
    'PartDesign::Solid': R_ABSTRACT,
    'PartDesign::Sphere': R_LEGACY_PRIMITIVE,
    'PartDesign::SubShapeBinderPython': R_ABSTRACT,
    'PartDesign::Torus': R_LEGACY_PRIMITIVE,
    'PartDesign::Transformed': R_ABSTRACT,
    'PartDesign::Wedge': R_LEGACY_PRIMITIVE,
    'Sketcher::SketchObjectPython': R_SKETCH_INTERNAL,
    'Sketcher::SketchObjectSF': R_SKETCH_INTERNAL,
}

def test_survey_owned_and_excluded_partition_the_134_surveyed_typeids_with_no_overlap():
    owned = set(SURVEYED_TYPEID_TO_OP)
    excluded = set(EXCLUDED_TYPEIDS)

    assert not (owned & excluded)
    assert len(owned) + len(excluded) == 134


def test_every_op_the_survey_uses_is_one_of_the_36_read_from_language_md():
    ops = _op_vocabulary()

    used = set(SURVEYED_TYPEID_TO_OP.values())
    assert used <= set(ops)
    assert len(ops) == 36


def test_every_surveyed_owned_typeid_is_accepted_under_its_op_in_op_typeids():
    missing = [(typeid, op) for typeid, op in SURVEYED_TYPEID_TO_OP.items()
               if typeid not in OP_TYPEIDS.get(op, ())]

    assert not missing, missing


def test_op_typeids_gives_no_typeid_to_two_ops_outside_the_one_named_exception():
    owner = {}
    collisions = []
    for op, typeids in OP_TYPEIDS.items():
        for typeid in typeids:
            if typeid in owner and owner[typeid] != op:
                collisions.append((typeid, owner[typeid], op))
            owner[typeid] = op

    unexplained = [c for c in collisions
                   if SHARED_TYPEID_EXCEPTIONS.get(c[0]) != tuple(sorted((c[1], c[2])))
                   and SHARED_TYPEID_EXCEPTIONS.get(c[0]) != (c[1], c[2])]
    assert not unexplained, unexplained
    for typeid, ops in SHARED_TYPEID_EXCEPTIONS.items():
        assert all(typeid in OP_TYPEIDS[op] for op in ops), typeid


def test_excluded_typeids_are_not_accepted_under_any_op():
    wrongly_accepted = [typeid for typeid in EXCLUDED_TYPEIDS
                         for typeids in OP_TYPEIDS.values() if typeid in typeids]

    assert not wrongly_accepted, wrongly_accepted


def test_hole_accepts_partdesign_pocket_as_well_as_partdesign_hole():
    """
    FCC-238: session afa0142baef847b0a9cd9139390af993 Step 5 cut a hole with
    a circular-sketch PartDesign::Pocket and was refused before Preflight,
    then retried onto PartDesign::Hole, which failed at runtime with "No
    base set, no sketch support either" — a working tool banned, replaced
    with a broken one, two revisions spent on the same operation.
    """
    assert OP_TYPEIDS["hole"] == ("PartDesign::Hole", "PartDesign::Pocket")

    code = "doc.addObject('PartDesign::Pocket', 'Hole001')"
    result = lint(code, op="hole")

    assert result.ok, result.report()


def test_pocket_op_still_refuses_a_typeid_neither_pocket_nor_hole_performs():
    code = "doc.addObject('Part::Extrusion', 'Pad001')"
    result = lint(code, op="hole")

    assert not result.ok
