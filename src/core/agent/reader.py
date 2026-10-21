"""
The reader arm: the drawing is read once into a table, then one agent builds with the drawing and the table in front of it.

A gate on the first `finish` measures the solid against the table and returns the differences, never a verdict, because an agent told its part conforms stops looking. It asks once, so it cannot loop.
"""
import json
import os
import tempfile

from core.agent.agent import _parsed, _say
from core.agent.solo import run_solo
from core.events import ERROR
from core.llm import Session
from core.loop.prompt import _as_image, _object_of
from core.prompts import read_prompt
from core.sheets import sheet_reader

EXTENT_TOLERANCE = (0.02, 0.5)
HOLLOW_FILL = 0.6
FLAT_BLANK_ASPECT = 0.05
DRAWING_VIEWS = ("front", "top", "right", "left", "iso")
THIN_FAMILIES = ("cover/tray", "cored casting")
NO_CHANGE = 1e-4

FACTS_CHECK = """
import json
from core.topology import betti_numbers

solids = [o.Shape for o in doc.Objects
          if not o.InList and getattr(o, "Shape", None) is not None
          and not o.Shape.isNull() and o.Shape.Solids]
facts = {"solid": bool(solids)}
if solids:
    shape = solids[0] if len(solids) == 1 else Part.Compound(solids)
    box = shape.BoundBox
    points, facets = shape.tessellate(0.001 * box.DiagonalLength)
    b0, b1, b2 = betti_numbers([(p.x, p.y, p.z) for p in points], facets)
    facts.update(extents=[box.XLength, box.YLength, box.ZLength], volume=shape.Volume,
                 faces=len(shape.Faces), b0=b0, b1=b1, b2=b2)
    for body in doc.Objects:
        if body.TypeId == "PartDesign::Body" and body.BaseFeature is not None:
            facts["base"] = {"volume": body.BaseFeature.Shape.Volume,
                             "faces": len(body.BaseFeature.Shape.Faces)}
print("FACTS " + json.dumps(facts))
"""


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


def parse_table(text):
    """
    The table a reply carries, with every field that is not usable dropped to `None`, or `None` where the reply holds no object.

    Tolerant on purpose: the table steers the build and the gate, and a half-readable one is still worth handing over.
    """
    said = _object_of(text or "")
    if not isinstance(said, dict):
        return None
    overall = said.get("overall") if isinstance(said.get("overall"), dict) else {}
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
                                     phase="read", stream=events))
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
        blocks += _blocks(sheet)
    else:
        blocks += [block for block in map(_as_image, attachments) if block]
    if table is not None:
        blocks.append({"type": "text", "text": "A first reading of the sheet:\n"
                                               + json.dumps(table, indent=1)})
    return blocks


def _close(measured, printed):
    return abs(measured - printed) <= max(EXTENT_TOLERANCE[0] * printed, EXTENT_TOLERANCE[1])


def mismatches(facts, table):
    """
    How the measured solid differs from the table and from the part it edits, largest first; empty where nothing differs.

    Only differences are listed, because a check that can say the part conforms is read as permission to stop.
    """
    if not facts.get("solid"):
        return ["The Document holds no solid."]
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
    if facts["b0"] != 1:
        found.append("The Document holds %d separate solids; the part is one." % facts["b0"])
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


def measured(kernel):
    """The facts of the Document's solid as the kernel sees it, or `None` where the measuring itself failed."""
    result = kernel.check(FACTS_CHECK, exploring=True)
    for line in reversed((result.stdout or "").splitlines()):
        if line.startswith("FACTS "):
            return json.loads(line[len("FACTS "):])
    return None


THROUGH_RULE = ("A pocket, slot or window goes through only where a section or hidden lines show it go "
                "through; otherwise cut it to its printed depth.")

COMPARE = ("Compare each view with the drawing. Name each feature of the drawing the part lacks or "
           "gets wrong, build it, then finish. " + THROUGH_RULE)


def _direction(name):
    """The standard view a table's name for a view stands for, by the first of the five it mentions; a section with no direction in its name is drawn from the front."""
    name = name.lower()
    found = [(name.index(word), word) for word in DRAWING_VIEWS if word in name]
    if found:
        return min(found)[1]
    return "front" if "section" in name else None


def gate(kernel, sheet, table, renders):
    """
    What to send back at the first `finish`: the differences found, the part in each view the table names beside that view's crop, and the order to compare them, with the names of the views drawn; `None` where there is neither a difference nor a picture to compare.
    """
    facts = measured(kernel)
    found = mismatches(facts, table) if facts else []
    blocks, drawn_views = [], {}
    for entry in (table or {}).get("views") or ():
        view, number = _direction(str(entry.get("view") or "")), _count(entry.get("crop"))
        if view is None or not number or number > len(sheet.crops):
            continue
        if view not in drawn_views:
            drawn = kernel.check("pass", render_path=os.path.join(renders, "gate-%s.png" % view),
                                 render_view=view)
            drawn_views[view] = _as_image(drawn.render) if drawn.ok else None
        if drawn_views[view]:
            blocks += [{"type": "text", "text": "Your part, %s view, then crop %d (%s) of the drawing:"
                                                % (view, number, entry.get("view"))},
                       drawn_views[view], _as_image(sheet.crops[number - 1].path)]
    if not found and not blocks:
        return None
    listed = ("What was measured on the solid:\n- " + "\n- ".join(found) + "\n\n") if found else ""
    return [{"type": "text", "text": listed + COMPARE}] + blocks, sorted(view for view, picture in drawn_views.items() if picture)


def run_reader(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None,
               max_turns=60, refresh=None, base=False):
    """
    Read the drawing once, then build with `run_solo` under the table and the gate; an edit (*base*) has no drawing to read and is gated on changing the part.
    """
    sheet = None if base else next(
        (found for found in map(sheet_reader.read, attachments) if found), None)
    table = read_table(sheet, llm, events) if sheet is not None else None
    with tempfile.TemporaryDirectory(prefix="fcc-gate-") as renders:
        return run_solo(intent, llm=llm, kernel=kernel, tools=tools, events=events,
                        attachments=attachments, cancelled=cancelled, max_turns=max_turns,
                        refresh=refresh, system=read_prompt("reader.md"),
                        opening=opening(intent, sheet, table, attachments, base),
                        gate=lambda: gate(kernel, sheet, table, renders))
