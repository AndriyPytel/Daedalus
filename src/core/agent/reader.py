"""
The reader arm: the drawing is read once into a table, then one agent works with the drawing and the table in front of it.

The Assertion, asked at the first `finish`, measures the solid against the table and returns the differences, never a verdict, because an agent told its part conforms stops looking. It asks once, so it cannot loop.
"""
import json
import os
import tempfile

from core.agent.agent import _say
from core.agent.domain import PART, Domain, unfinished as domain_unfinished
from core.agent.solo import run_solo
from core.events import ERROR, FAILED, WARNING
from core.llm import IMAGE_SUFFIXES, PDF_SUFFIXES, Session
from core.loop.results import LoopResult
from core.loop.prompt import _as_image, _attached, _object_of
from core.prompts import read_prompt
from core.sheets import sheet_reader

EXTENT_TOLERANCE = (0.02, 0.5)
HOLLOW_FILL = 0.6
FLAT_BLANK_ASPECT = 0.05
MAX_TURNS = 60
NOT_READ = "drawing not read"
DRAWING_VIEWS = ("front", "top", "right", "left", "iso")
THIN_FAMILIES = ("cover/tray", "cored casting")
NO_CHANGE = 1e-4
MESH_CHECK_TIMEOUT = 90

PART_FACTS = """
import json
from core.agent.solids import top_solids
from core.manifold import mesh_facts_fc
from core.topology import betti_numbers

roots = top_solids(doc)
facts = {"solid": bool(roots)}
if roots:
    shape = roots[0].Shape if len(roots) == 1 else Part.Compound([o.Shape for o in roots])
    box = shape.BoundBox
    points, facets = shape.tessellate(0.001 * box.DiagonalLength)
    b0, b1, b2 = betti_numbers([(p.x, p.y, p.z) for p in points], facets)
    facts.update(solids=len(shape.Solids), extents=[box.XLength, box.YLength, box.ZLength],
                 volume=shape.Volume, faces=len(shape.Faces), b0=b0, b1=b1, b2=b2, tree_faults=[])
    for o in roots:
        if o.TypeId != "PartDesign::Body":
            facts["tree_faults"].append("%s is a %s, not a PartDesign::Body" % (o.Name, o.TypeId))
        elif o.Tip is None or not o.Tip.TypeId.startswith("PartDesign::") \\
                or not any(f.TypeId.startswith("PartDesign::") for f in o.Group):
            facts["tree_faults"].append("Body %s has no PartDesign feature as its Tip" % o.Name)
    for body in doc.Objects:
        if body.TypeId == "PartDesign::Body" and body.BaseFeature is not None:
            facts["base"] = {"volume": body.BaseFeature.Shape.Volume,
                             "faces": len(body.BaseFeature.Shape.Faces)}
    if not facts["tree_faults"]:
        facts.update(mesh_facts_fc(shape))
print("FACTS " + json.dumps(facts))
"""

NO_TREE = ("The Document's solid is not a feature tree: %s. Build the part in a PartDesign::Body "
           "from sketches and PartDesign features (Pad, Pocket, Revolution, ...) so it can be "
           "edited.")
UNMEASURABLE = "The Document could not be measured. Check that it recomputes, then finish."


def unfinished_part(kernel):
    """Why the Part Domain is not finished: the feature tree, the solid count and the grader's mesher and STEP reader, or `None`."""
    return domain_unfinished(kernel, PART_FACTS, lambda facts: refusals(facts, None, asserting=False),
                             "Part", UNMEASURABLE, MESH_CHECK_TIMEOUT)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _count(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _unusable(said, overall):
    """The names of the fields the reply gave and `parse_table` cannot use."""
    lost = ["overall.%s" % axis for axis in "xyz"
            if overall.get(axis) is not None and _number(overall.get(axis)) is None]
    if said.get("overall") is not None and not isinstance(said["overall"], dict):
        lost.append("overall")
    for name in ("views", "dimensions", "hole_patterns"):
        value = said.get(name)
        if value is not None and (not isinstance(value, list) or len(_dicts(value)) < len(value)):
            lost.append(name)
    if said.get("through_count") is not None and _count(said["through_count"]) is None:
        lost.append("through_count")
    return lost


def parse_table(text, events=None):
    """
    The table a reply carries, with every field that is not usable dropped to `None` and named in a WARNING on *events*, or `None` where the reply holds no object.

    Tolerant on purpose: the table steers the build and the Assertion, and a half-readable one is still worth handing over.
    """
    said = _object_of(text or "")
    if not isinstance(said, dict):
        return None
    overall = said.get("overall") if isinstance(said.get("overall"), dict) else {}
    lost = _unusable(said, overall)
    if lost and events is not None:
        events.emit(WARNING, message="the table read off the sheet lost unusable fields: %s" % ", ".join(lost))
    thin = said.get("thin_walled")
    return {
        "views": _dicts(said.get("views")),
        "body_family": str(said.get("body_family") or ""),
        "overall": {axis: _number(overall.get(axis)) for axis in "xyz"},
        "dimensions": _dicts(said.get("dimensions")),
        "hole_patterns": _dicts(said.get("hole_patterns")),
        "through_count": _count(said.get("through_count")),
        "thin_walled": thin is True or str(thin).lower() in ("true", "yes"),
        "flat_pattern_present": said.get("flat_pattern_present") is True,
    }


def _blocks(sheet):
    """The sheet as content blocks: the overview, then each crop under its number and name."""
    blocks = [{"type": "text", "text": "The sheet whole, small, for orientation only:"},
              _as_image(sheet.overview)]
    for number, crop in enumerate(sheet.crops, start=1):
        blocks += [{"type": "text", "text": "Crop %d (%s):" % (number, crop.name)},
                   _as_image(crop.path)]
    return blocks


def read_table(sheet, llm, events):
    """The table read off *sheet* in one call, retried once, or `None` where neither reply holds one."""
    content = [{"type": "text", "text": "Read this drawing."}] + _blocks(sheet)
    for _ in range(2):
        try:
            table = parse_table(_say(Session(system=read_prompt("read_table.md")), llm, content,
                                     phase="read", stream=events), events)
        except Exception as exc:
            events.emit(ERROR, message="the reading call failed: %s: %s" % (type(exc).__name__, exc))
            continue
        if table is not None:
            return table
    return None


def opening(intent, sheet, table, attachments, base):
    """The build loop's first message: the intent, the sheet's pictures, the table, and for an edit the note that the part is already in the Document."""
    blocks = [{"type": "text", "text": intent}]
    if base:
        blocks.append({"type": "text", "text": "The Document already holds the part to edit as the "
                                               "BaseFeature of its Body; edit it in place."})
    if sheet is not None:
        blocks += _blocks(sheet) + _attached([p for p in attachments
                                              if not p.lower().endswith(IMAGE_SUFFIXES + PDF_SUFFIXES)])
    else:
        blocks += _attached(attachments)
    if table is not None:
        blocks.append({"type": "text", "text": "A first reading of the sheet:\n"
                                               + json.dumps(table, indent=1)})
    return blocks


def _close(measured, printed):
    return abs(measured - printed) <= max(EXTENT_TOLERANCE[0] * printed, EXTENT_TOLERANCE[1])


def refusals(facts, table, asserting=True):
    """
    What fails of the Part Domain over *facts*, in the order the agent can mend it, empty where nothing fails; with *asserting*, also how the solid differs from the table and from the part it edits.

    Only differences are listed, because a check that can say the part conforms is read as permission to stop.
    """
    if not facts["solid"]:
        return [NO_TREE % "the Document holds no solid"]
    found = []
    if facts["tree_faults"]:
        found.append(NO_TREE % "; ".join(facts["tree_faults"]))
    if facts["solids"] > 1:
        found.append("The Document holds %d separate solids; the part is one. Fuse them into one."
                     % facts["solids"])
    if facts.get("unmeshable"):
        found.append("The grader could not tessellate this solid: %s." % facts["unmeshable"])
    if facts.get("step_readback") is False:
        found.append("The STEP written from this solid does not read back valid and manifold, so the "
                     "grader would refuse it. Simplify the faces it fails on.")
    return found + (_differences(facts, table) if asserting else [])


def _differences(facts, table):
    found = []
    extents = facts["extents"]
    if table is not None and not table["flat_pattern_present"]:
        given = sorted(value for value in table["overall"].values() if value is not None)
        pool = sorted(extents)
        if len(given) == 3:
            pairs = zip(given, pool)
        else:
            pairs = ((value, min(pool, key=lambda e: abs(e - value))) for value in given)
        for read, built in pairs:
            if not _close(built, read):
                found.append("The table read %g mm for an overall dimension; the solid measures %.1f mm "
                             "there. Check it on the crop." % (read, built))
    if table is not None:
        box = extents[0] * extents[1] * extents[2]
        hollow = table["thin_walled"] or table["body_family"] in THIN_FAMILIES
        if hollow and box and facts["volume"] / box > HOLLOW_FILL:
            found.append("The drawing shows a thin-walled part, but the solid fills %.0f%% of its "
                         "bounding box; shell or pocket it." % (100 * facts["volume"] / box))
        if table["flat_pattern_present"] and min(extents) < FLAT_BLANK_ASPECT * max(extents):
            found.append("The drawing shows a flat pattern and formed views; the solid is a flat plate "
                         "(%s mm). Build the formed part." % " x ".join("%.1f" % e for e in extents))
    base = facts.get("base")
    if base and abs(facts["volume"] - base["volume"]) <= NO_CHANGE * base["volume"] \
            and facts["faces"] == base["faces"]:
        found.append("The part is the same as the one you were given to edit: same volume, same face "
                     "count. The edit has not been made.")
    return found


COMPARE = ("Compare each view with the drawing. Name each feature of the drawing the part lacks or "
           "gets wrong, build it, then finish.")


def _direction(name):
    """The standard view a table's name for a view stands for, by the first of the five it mentions; a section with no direction in its name is drawn from the front."""
    name = name.lower()
    found = [(name.index(word), word) for word in DRAWING_VIEWS if word in name]
    if found:
        return min(found)[1]
    return "front" if "section" in name else None


def assert_part(kernel, sheet, table, renders, events):
    """
    What to send back at the first `finish`: the differences found, the part in each view the table names beside that view's crop, and the order to compare them, with the names of the views drawn; `None` where there is neither a difference nor a picture to compare.
    """
    facts = kernel.measure(PART_FACTS, timeout=MESH_CHECK_TIMEOUT)
    found = refusals(facts, table) if facts else []
    blocks, drawn_views = [], {}
    for entry in (table or {}).get("views") or ():
        view, number = _direction(str(entry.get("view") or "")), _count(entry.get("crop"))
        if view is None or not number or number > len(sheet.crops):
            events.emit(WARNING, message="the Assertion dropped the view %r (crop %s): no standard view "
                                         "or no such crop on the sheet" % (entry.get("view"), entry.get("crop")))
            continue
        if view not in drawn_views:
            drawn = kernel.render(os.path.join(renders, "assertion-%s.png" % view), view)
            drawn_views[view] = _as_image(drawn.render) if drawn.ok else None
            if not drawn_views[view]:
                events.emit(WARNING, message="the Assertion dropped the %s view: the part could not be drawn" % view)
        if drawn_views[view]:
            blocks += [{"type": "text", "text": "Your part, %s view, then crop %d (%s) of the drawing:"
                                                % (view, number, entry.get("view"))},
                       drawn_views[view], _as_image(sheet.crops[number - 1].path)]
    if not found and not blocks:
        return None
    listed = ("What was measured on the solid:\n- " + "\n- ".join(found) + "\n\n") if found else ""
    return [{"type": "text", "text": listed + COMPARE}] + blocks, sorted(view for view, picture in drawn_views.items() if picture)


def run_reader(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None, base=False):
    """
    Read the drawing once, then build with `run_solo` under the table and the Assertion; an edit (*base*) has no drawing to read and is asserted on changing the part.
    A drawing that two reads could not turn into a table ends the run, since a build without it has nothing to be measured against.
    """
    sheet = None
    for path in () if base else attachments:
        sheet = sheet_reader.read(path)
        if sheet:
            break
        if path.lower().endswith(IMAGE_SUFFIXES + PDF_SUFFIXES):
            events.emit(WARNING, message="%s gave no sheet: it is unreadable, or pymupdf or PIL is missing"
                        % os.path.basename(path))
    table = read_table(sheet, llm, events) if sheet is not None else None
    if sheet is not None and table is None:
        events.emit(FAILED, message=NOT_READ)
        return LoopResult(ok=False, reason=NOT_READ)
    with tempfile.TemporaryDirectory(prefix="fcc-assertion-") as renders:
        domain = Domain(PART, read_prompt("reader.md"), opening(intent, sheet, table, attachments, base),
                        lambda: assert_part(kernel, sheet, table, renders, events), unfinished_part)
        return run_solo(intent, domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                        attachments=attachments, cancelled=cancelled, max_turns=MAX_TURNS)
