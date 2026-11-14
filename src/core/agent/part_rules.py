"""
The Part Domain's rules for a Step: objects left in error, an idle feature, an unwired feature, a Body in pieces, invalid, inverted or not manifold, a fillet over mixed concavity.
A Kernel seeded with this module calls `precheck` before the Step's recompute, `check` after it, and appends `hints` to the Step's failure; a Kernel seeded with no rules calls none.
"""
from core.kernel.step_namespace import group_edges_by_sign, volume_moved, volume_of


def _features_not_wired_to_a_body(doc):
    """
    Every PartDesign feature the Document holds that no Body's Group claims.

    `doc.addObject('PartDesign::Pad', ...)` computes the feature's own
    Shape from its Profile regardless of who owns it, so it looks correct
    and Up-to-date; only the Body it was meant to extend is left with
    `Tip` unset and its own `Shape.Volume` raising "shape is invalid".
    """
    wired = {member.Name for obj in doc.Objects if obj.TypeId == "PartDesign::Body"
             for member in obj.Group}
    return [obj.Name for obj in doc.Objects
            if obj.isDerivedFrom("PartDesign::Feature") and obj.Name not in wired]


def _bodies_with(doc, defect):
    """
    The lines *defect* reports, one per PartDesign Body whose Shape it rejects.

    Only Bodies -- a Part::Compound is many solids on purpose.
    """
    found = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Body":
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        said = defect(shape)
        if said:
            found.append("%s: %s" % (obj.Name, said))
    return found


def _in_pieces(shape):
    if len(shape.Solids) > 1:
        return "%d separate solids" % len(shape.Solids)


def _invalid(shape):
    if not shape.isValid():
        return "invalid shape"


def _inverted(shape):
    if shape.Volume < 0:
        return "inverted solid (volume %s)" % shape.Volume


def _pinched(shape):
    from core.manifold import check_fc

    return check_fc(shape)


def _not_round_trip_safe(shape):
    from core.manifold import step_round_trip_fc

    return step_round_trip_fc(shape)


_BODY_CHECKS = (
    (_in_pieces, "this Step left a Body in more than one piece:",
     "A PartDesign Body is one solid. Disjoint profiles in one sketch pad into "
     "separate lumps rather than one part -- the profiles that make up the outline "
     "have to meet, and anything that is genuinely a second part belongs in a "
     "second Body."),
    (_invalid, "this Step left a Body with an invalid Shape:",
     "OCC's own isValid() says the geometry is broken -- a self-touching wire or "
     "a face that folds back on itself, not a Body it will let through unnoticed."),
    (_inverted, "this Step left a Body with an inverted solid:",
     "A negative signed volume is a solid that came out inside-out -- a boss cut "
     "instead of added, a profile wound the wrong way."),
    (_pinched, "this Step left a Body that is not manifold:",
     "A cylinder face exactly tangent to another face leaves a wall of zero "
     "thickness there. OCC calls it valid, but the exported STEP fails the mesh check "
     "every consumer runs -- move the cylinder or change its radius so the faces "
     "overlap or leave a gap."),
    (_not_round_trip_safe, "this Step left a Body that does not survive a STEP round trip:",
     "OCC calls the Body valid, but the STEP written from it reads back with broken faces and the "
     "exported file is rejected as invalid. Coplanar faces left unmerged (Refine off), or a feature "
     "leaving a sliver or a ledge face that coincides with an earlier feature's face, make it -- "
     "rebuild the Step so the faces merge or overlap clearly."),
)


def _body_defects(doc):
    """
    The refusal for the first of `_BODY_CHECKS` some Body fails, or None; later checks are not run.

    Each check catches what recomputes Up-to-date and is still wrong: disjoint lumps, a
    self-touching wire, a solid inside-out, a wall of zero thickness OCC calls valid.
    """
    for defect, heading, advice in _BODY_CHECKS:
        found = _bodies_with(doc, defect)
        if found:
            return "%s\n%s\n%s" % (heading, "\n".join(found), advice)
    return None


def _fillets_with_mixed_concavity(doc):
    """
    Every `PartDesign::Fillet` whose `Base` edges are not all one concavity.

    A single radius on a Base that mixes concave and convex edges pulls half
    a rim outward and half inward -- the report still says the Step
    succeeded, because a mixed selection recomputes clean.
    """
    mixed = []
    for obj in doc.Objects:
        if obj.TypeId != "PartDesign::Fillet":
            continue
        base = getattr(obj, "Base", None)
        if not base or not base[0]:
            continue
        feature, names = base
        shape = getattr(feature, "Shape", None)
        if shape is None or shape.isNull():
            continue
        by_sign = group_edges_by_sign(shape, names)
        if len(by_sign) > 1:
            sides = ", ".join("%s: %s" % ("concave" if sign > 0 else "convex", ", ".join(edges))
                              for sign, edges in sorted(by_sign.items(), reverse=True))
            mixed.append("%s: Base mixes concave and convex edges (%s)" % (obj.Name, sides))
    return mixed


def precheck(doc):
    """
    Refuse a mixed-concavity `Base` with its own sentence before OCC's
    `command not done` or `FrozenShape` can bury it under a generic
    recompute-error message.

    Called once before the recompute -- against Base edges resolved from a
    base feature the Step's own code already recomputed, so the check needs
    no recompute of its own -- and once after, since a base feature this
    same Step just created has no Shape yet at the first call and only
    becomes checkable once the recompute below gives it one.
    """
    mixed = _fillets_with_mixed_concavity(doc)
    if mixed:
        raise RuntimeError(
            "this Step built a fillet whose Base mixes concavities:\n" + "\n".join(mixed)
            + "\nOne radius on a mixed selection pulls the concave edges inward and the "
              "convex edges outward -- split Base by concavity (a face's own normal against "
              "the other face's CenterOfMass at the edge's midpoint) and fillet one side "
              "at a time.")


_A_DRESSUP = {"PartDesign::Fillet": ("Radius", "makeFillet"),
              "PartDesign::Chamfer": ("Size", "makeChamfer")}


_SMALLER = (0.75, 0.5, 0.25)


def _why_it_failed(obj):
    """What the Document says about a feature in error, and what it will not."""
    return ". ".join(said for said in (obj.getStatusString(),
                                       _why_a_dressup_failed(obj)) if said)


def _objects_left_in_error(doc):
    """Every object a recompute left `Invalid`, one line each."""
    return ["%s (%s): %s" % (o.Name, o.TypeId, _why_it_failed(o))
            for o in doc.Objects if o.State and "Invalid" in o.State]


def _why_a_dressup_failed(obj):
    """
    What a fillet or a chamfer will not say for itself.

    Constraint: a refused dressup names no edge and no size, so the
    selection is rebuilt on the base feature to find which and whether the
    size fits, tried at `_SMALLER` -- three quarters, a half, a quarter.
    """
    how = _A_DRESSUP.get(obj.TypeId)
    base = getattr(obj, "Base", None)
    if how is None or not base:
        return ""
    of_it, made = how
    size = getattr(obj, of_it, None)
    size = getattr(size, "Value", size)
    shape = getattr(base[0], "Shape", None)
    if not size or shape is None or shape.isNull():
        return ""

    chosen = _the_edges_of(shape, base[1])
    refused = _refusal(shape, made, size, [edge for _, edge in chosen]) if chosen else ""
    if not refused:
        return ""

    said = ("the same selection built on %s is refused with %s"
            % (base[0].Name, refused))
    for how_many in range(1, len(chosen) + 1):
        if _refusal(shape, made, size, [edge for _, edge in chosen[:how_many]]):
            said += (". %s is the first of the %d selected that will not build with the "
                     "edges before it" % (chosen[how_many - 1][0], len(chosen)))
            break
    for share in _SMALLER:
        if not _refusal(shape, made, size * share, [edge for _, edge in chosen]):
            said += ". The whole selection goes through at %s mm" % round(size * share, 3)
            break
    return said


def _the_edges_of(shape, chosen):
    """
    The edges a dressup's sub-element names stand for, each under its own name.

    A dressup is selected by edge or by face, and a face stands for every edge
    of its outline. Named against the base shape rather than against the face,
    so what comes back is what a Step can be written with.
    """
    edges = []
    for name in chosen:
        try:
            element = shape.getElement(name)
        except Exception:
            continue
        for edge in getattr(element, "Edges", [element]):
            named = _the_name_of(shape, edge)
            if named not in [was for was, _ in edges]:
                edges.append((named, edge))
    return edges


def _the_name_of(shape, edge):
    for at, one in enumerate(shape.Edges, start=1):
        if one.isSame(edge):
            return "Edge%d" % at
    return "an edge"


def _refusal(shape, made, size, edges):
    """What the same dressup raises at Part level, or "" where it goes through."""
    try:
        getattr(shape, made)(size, edges)
    except Exception as exc:
        return str(exc).strip() or exc.__class__.__name__
    return ""


_VOLUME_TOLERANCE = 1e-9


def _features_that_moved_no_material(doc, created):
    """
    Features this Step created that came out the same volume they went in.

    A Pocket sketched on the face at YMin with its normal pointing into the
    part cuts along -Y, away from the solid, and hands back its base shape
    untouched. FreeCAD calls it 'Up-to-date' and 'Valid'.
    """
    idle = []
    for name, volume in volume_moved(doc, created).items():
        before, now = volume["before"], volume["after"]
        if abs(now - before) <= _VOLUME_TOLERANCE * max(1.0, abs(before)):
            idle.append((doc.getObject(name), before, now))
    return idle


def _idle_feature_cause(before, after_reversed_flip):
    """
    3 of 12 recorded volume-neutral failures moved volume when `Reversed`
    was flipped (profile on the right face pointing the wrong way), the
    other 9 did not (profile that overlaps the solid at neither sign).
    """
    if abs(after_reversed_flip - before) > _VOLUME_TOLERANCE * max(1.0, abs(before)):
        return "direction"
    return "no-overlap"


def _diagnose_idle_feature(obj, before, now):
    """
    Tell a wrong-direction Pocket from one whose profile never touches the
    solid, by flipping `Reversed` and recomputing.

    Returns the cause and, for `"direction"`, the `Reversed` value opposite
    the one already on the object -- the value that would fix the cut.
    """
    if not hasattr(obj, "Reversed"):
        return None, None
    original = obj.Reversed
    try:
        obj.Reversed = not original
        obj.Document.recompute([obj])
        flipped = volume_of(obj)
    finally:
        obj.Reversed = original
        obj.Document.recompute([obj])
    return _idle_feature_cause(before, flipped), not original


_IDLE_CAUSE_MESSAGE = {
    "direction": (
        "flipping Reversed changed its volume: the profile sits on the right "
        "face but the cut travels away from the solid instead of into it. Set "
        "Reversed=%s (or use Type='ThroughAll' or SideType='Symmetric' for a "
        "through cut rather than a length that may run the wrong way)."
    ),
    "no-overlap": (
        "flipping Reversed left its volume unchanged either way: the profile "
        "does not overlap the solid at all, at either sign of Reversed. Check "
        "the sketch's placement against the Body's BoundBox -- Reversed will "
        "not fix this."
    ),
    None: (
        "flipping Reversed is not possible on this feature type, so the check "
        "cannot tell a wrong direction from a placement that misses the solid. "
        "Compare the feature's placement against the Body's BoundBox by hand."
    ),
}


_IDLE_HEADLINE = "this Step added a feature that neither removed nor added any material"


_IDLE_EXPLANATION = (
    "A Pad, Pocket, Hole or Fillet that leaves the Body's volume exactly as it found it "
    "did not happen: a Pocket whose profile is extruded away from the solid cuts nothing."
)


def _idle_feature_report(doc, created):
    """
    What this Step's volume-neutral features did, as the hint a failure carries.

    A Step's own volume assert raises before any check in `_run_step_body`
    runs, so every failure carries this and not only the clean runs. A hint is
    never worth losing the traceback it explains, but the diagnostic's own
    exception is not thrown away either: it is reported in its place.
    """
    try:
        idle = _features_that_moved_no_material(doc, created)
        if not idle:
            return ""
        def _cause_message(obj, before, now):
            cause, fix = _diagnose_idle_feature(obj, before, now)
            message = _IDLE_CAUSE_MESSAGE[cause]
            return message % fix if cause == "direction" else message

        lines = [
            "%s (%s): %s mm3 before, %s mm3 after -- %s"
            % (obj.Name, obj.TypeId, before, now, _cause_message(obj, before, now))
            for obj, before, now in idle
        ]
        return "\n%s\n%s" % ("\n".join(lines), _IDLE_EXPLANATION)
    except BaseException as exc:
        return "\n(the volume-neutral feature diagnostic itself raised: %s)" % exc


def _past_the_tip(doc, created):
    """
    The features this call added to a Body after its Tip, which build nothing
    into `body.Shape` whatever their own Shape shows. `Body.addObject` moves
    the Tip onto a Pad but leaves it where it was for a PolarPattern.
    """
    lines = []
    for body in doc.Objects:
        if body.TypeId != "PartDesign::Body" or body.Tip is None:
            continue
        group = body.Group
        tip = group.index(body.Tip) if body.Tip in group else len(group)
        past = [obj for obj in group[tip + 1:]
                if obj.Name in created and obj.isDerivedFrom("PartDesign::Feature")]
        if past:
            lines.append("%s is past %s's Tip (%s), so %s.Shape does not have it: "
                         "set body.Tip = doc.getObject('%s') and recompute."
                         % (", ".join(obj.Name for obj in past), body.Name, body.Tip.Name,
                            body.Name, past[-1].Name))
    return "\n" + "\n".join(lines) if lines else ""


def check(doc, touched):
    """
    Raise on whatever a Step left wrong in *doc*: objects in error, idle features, unwired features, a Body in pieces, invalid, inverted or not manifold.
    A Step that changed nothing in a Document it did not open cannot have left any of it, and these scans cost seconds on a big part.
    """
    failures = _objects_left_in_error(doc)
    if failures:
        raise RuntimeError("recompute left these objects in error:\n" + "\n".join(failures))

    precheck(doc)

    if _features_that_moved_no_material(doc, touched.created):
        raise RuntimeError(_IDLE_HEADLINE)

    orphaned = _features_not_wired_to_a_body(doc)
    if orphaned:
        raise RuntimeError(
            "this Step created a feature no Body claims:\n" + "\n".join(orphaned)
            + "\nA feature added with doc.addObject computes its own Shape from its "
              "Profile and looks correct on its own, but the Body it was meant to extend "
              "still has none -- its own Shape.Volume raises 'shape is invalid' until the "
              "feature is wired in with body.addObject(feature) and body.Tip = feature, "
              "the same way the Body's other features were added.")

    refusal = _body_defects(doc)
    if refusal:
        raise RuntimeError(refusal)


def hints(doc, created):
    """What this Step's features did that its failure should carry: the idle ones, and those added past a Body's Tip."""
    return _idle_feature_report(doc, created) + _past_the_tip(doc, created)
