"""
The BIM Domain: the agent writes FreeCAD Python against the Arch workbench, and `finish` is refused until the Document holds one flat as native BIM objects.
That is at least one Wall, Slab, Door, Window and Space, each Door and Window hosted by a Wall, everything under a Building or Level, no Wall of zero size, an IFC4 export ifcopenshell reopens, and every piece of Equipment with an allowed Source and a Level; a Document of two or more Levels also needs one Building over them, each Level holding Walls and a Slab, and a Stairs between each two inside the lower Level's walls.
The facts are measured in the kernel and judged here.
"""
from core import arch, furniture
from core.agent.domain import BIM, Domain, unfinished as domain_unfinished
from core.agent.solo import run_solo
from core.loop.prompt import _attached
from core.prompts import read_prompt

MAX_TURNS = 80
LEVEL_APART = 1.0
STAIRS_TOLERANCE = 20.0
FURNITURE = "IfcFurnishingElement"
KINDS = {"Wall": "IfcWall", "Slab": "IfcSlab", "Door": "IfcDoor", "Window": "IfcWindow", "Space": "IfcSpace"}

FACTS_TAIL = """
with open(FACTS_PATH, "w", encoding="utf-8") as out:
    json.dump(facts, out)
print("FACTS @")
"""

FACTS_CHECK = """
import hashlib
import json
import os
import tempfile

from core.arch import arch_type, export_ifc
from core.furniture import short

PLACES = ("Building", "Building Storey")


def kind(obj):
    found = arch_type(obj)
    return "Slab" if found == "Structure" and obj.IfcType == "Slab" else found


def placed(obj, places=PLACES):
    parents = [p for p in obj.InList if obj in (getattr(p, "Group", None) or ())]
    return any(arch_type(p) == "BuildingPart" and p.IfcType in places or placed(p, places) for p in parents)


def unplaced(obj):
    if kind(obj) not in ("Wall", "Slab", "Window", "Space") or placed(obj):
        return False
    return not (kind(obj) == "Window" and any(placed(host) for host in obj.Hosts))


def hosts(obj):
    return [h.Label for h in obj.Hosts if arch_type(h) == "Wall"]


def source(obj):
    return getattr(obj, "Source", "")


def length(wall):
    base = wall.Base
    if base is not None and getattr(base, "Shape", None) is not None and not base.Shape.isNull():
        return base.Shape.Length
    return wall.Length.Value


doc.recompute()
objects = [o for o in doc.Objects if arch_type(o)]


def solid(obj):
    return obj.Shape if not obj.Shape.isNull() else None


def signature(obj):
    shape = solid(obj)
    box = shape.BoundBox if shape else None
    return (obj.Label, round(shape.Volume) if shape else 0,
            tuple(round(v) for v in (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)) if box else (),
            tuple(sorted(h.Label for h in getattr(obj, "Hosts", ()))))


def members(level):
    found = []
    for child in level.Group:
        found.append(child)
        if arch_type(child) == "BuildingPart":
            found += members(child)
    return found


def box_of(shapes):
    boxes = [shape.BoundBox for shape in shapes if shape is not None]
    if not boxes:
        return None
    return [round(min(b.XMin for b in boxes), 1), round(min(b.YMin for b in boxes), 1),
            round(max(b.XMax for b in boxes), 1), round(max(b.YMax for b in boxes), 1)]


def top(obj):
    shape = solid(obj)
    return round(shape.BoundBox.ZMax + obj.RiserHeight.Value, 1) if shape else 0.0


levels = [o for o in objects if kind(o) == "BuildingPart" and o.IfcType == "Building Storey"]
openings = [o for o in objects if kind(o) == "Window"]
facts = {
    "signature": hashlib.sha1(repr(sorted(map(signature, objects))).encode()).hexdigest(),
    "walls": [{"label": o.Label, "length": round(length(o), 1), "height": round(o.Height.Value, 1),
               "width": round(o.Width.Value, 1), "volume": round(solid(o).Volume) if solid(o) else 0}
              for o in objects if kind(o) == "Wall"],
    "slabs": [o.Label for o in objects if kind(o) == "Slab"],
    "doors": [{"label": o.Label, "hosts": hosts(o)} for o in openings if o.IfcType == "Door"],
    "windows": [{"label": o.Label, "hosts": hosts(o)} for o in openings if o.IfcType != "Door"],
    "spaces": [o.Label for o in objects if kind(o) == "Space"],
    "buildings": len([o for o in objects if kind(o) == "BuildingPart" and o.IfcType == "Building"]),
    "unplaced": [o.Label for o in objects if unplaced(o)],
    "levels": [{"label": o.Label, "elevation": round(o.Placement.Base.z, 1),
                "walls": len([m for m in members(o) if kind(m) == "Wall"]),
                "slabs": len([m for m in members(o) if kind(m) == "Slab"]),
                "building": placed(o, ("Building",)),
                "box": box_of(solid(m) for m in members(o) if kind(m) == "Wall")} for o in levels],
    "stairs": [{"label": o.Label, "bottom": round(solid(o).BoundBox.ZMin, 1) if solid(o) else 0.0, "top": top(o),
                "level": placed(o, ("Building Storey",)), "box": box_of([solid(o)])}
               for o in objects if kind(o) == "Stairs"],
    "equipment": [{"label": o.Label, "source": short(source(o)), "level": placed(o, ("Building Storey",))}
                  for o in objects if kind(o) == "Equipment"],
}
handle, path = tempfile.mkstemp(suffix=".ifc")
os.close(handle)
try:
    import ifcopenshell

    export_ifc(doc, path)
    model = ifcopenshell.open(path)
    facts["ifc"] = {"schema": model.schema, **{t: len(model.by_type(t)) for t in %(types)s}}
except Exception as exc:
    facts["ifc"] = {"error": "%%s: %%s" %% (type(exc).__name__, exc)}
finally:
    os.remove(path)
""" % {"types": (*KINDS.values(), FURNITURE)} + FACTS_TAIL


NATIVE_FACTS_CHECK = """
import hashlib
import json
import os
import tempfile

import ifcopenshell
import ifcopenshell.util.element as element_util

from core import nativeifc

KINDS = %(kinds)s
handle, path = tempfile.mkstemp(suffix=".ifc")
os.close(handle)
try:
    nativeifc.export(doc, path)
    model = ifcopenshell.open(path)
    with open(path, encoding="utf-8") as saved:
        body = sorted(line for line in saved.read().splitlines() if line.startswith("#"))
finally:
    os.remove(path)


def name(element):
    return element.Name or "#%%d" %% element.id()


def wall_of(opening):
    for void in opening.VoidsElements:
        if void.RelatingBuildingElement.is_a("IfcWall"):
            return void.RelatingBuildingElement
    return None


def host(element):
    for fill in element.FillsVoids:
        if wall_of(fill.RelatingOpeningElement):
            return wall_of(fill.RelatingOpeningElement)
    return None


def storey_of(element):
    found = element_util.get_container(element) or element_util.get_aggregate(element)
    return found is not None and found.is_a("IfcBuildingStorey")


def placed(element):
    wall = host(element) if element.is_a() in ("IfcDoor", "IfcWindow") else None
    return storey_of(element) or (wall is not None and storey_of(wall))


openings = model.by_type("IfcDoor") + model.by_type("IfcWindow")
facts = {
    "signature": hashlib.sha1("\\n".join(body).encode()).hexdigest(),
    "schema": model.schema,
    "counts": {kind: len(model.by_type(kind)) for kind in KINDS},
    "labels": {kind: [name(e) for e in model.by_type(kind)] for kind in KINDS},
    "openings": len([o for o in model.by_type("IfcOpeningElement") if wall_of(o)]),
    "unfilled": [name(e) for e in openings if host(e) is None],
    "orphans": [name(o) for o in model.by_type("IfcOpeningElement") if wall_of(o) and not o.HasFillings],
    "unplaced": [name(e) for kind in KINDS for e in model.by_type(kind) if not placed(e)],
}
""" % {"kinds": tuple(KINDS.values())} + FACTS_TAIL


def library_index():
    return furniture.index(furniture.CACHE, furniture.http_get)


def building_refusals(facts):
    """What fails of the multi-storey Assertion over the Levels and Stairs of *facts*."""
    found = []
    if facts["buildings"] > 1:
        found.append("The Document holds %d Buildings. Put every Level under one Arch.makeBuilding()." % facts["buildings"])
    levels = sorted(facts["levels"], key=lambda level: level["elevation"])
    for level in levels:
        if not level["building"]:
            found.append("Level %s is not under the Building. Add it to the Building's Group." % level["label"])
        for key, kind in (("walls", "Wall"), ("slabs", "Slab")):
            if not level[key]:
                found.append("Level %s holds no %s. Add it to the Level's Group." % (level["label"], kind))
    for lower, upper in zip(levels, levels[1:]):
        if upper["elevation"] - lower["elevation"] <= LEVEL_APART:
            found.append("Level %s is at elevation %g mm, the same as Level %s. Set the Level's Placement.Base.z."
                         % (upper["label"], upper["elevation"], lower["label"]))
    for stair in facts["stairs"]:
        if not stair["level"]:
            found.append("Stairs %s is not under a Level. Add it to a Level's Group." % stair["label"])
    matched = set()
    for lower, upper in zip(levels, levels[1:]):
        between = [s for s in facts["stairs"] if abs(s["bottom"] - lower["elevation"]) <= STAIRS_TOLERANCE
                   and abs(s["top"] - upper["elevation"]) <= STAIRS_TOLERANCE]
        matched.update(s["label"] for s in between)
        if not between:
            found.append("No Stairs runs from Level %s (%g mm) up to Level %s (%g mm): its bottom is at the lower Level "
                         "and its top, the highest tread plus one riser, within %g mm of the upper one."
                         % (lower["label"], lower["elevation"], upper["label"], upper["elevation"], STAIRS_TOLERANCE))
        elif len(between) > 1:
            found.append("%s run from Level %s up to Level %s. Keep one."
                         % (" and ".join(s["label"] for s in between), lower["label"], upper["label"]))
        for stair in between:
            if lower["box"] is None or not _within(stair["box"], lower["box"]):
                found.append("Stairs %s lies outside the walls of Level %s. Place its footprint inside them."
                             % (stair["label"], lower["label"]))
    for stair in facts["stairs"]:
        if stair["label"] not in matched:
            found.append("Stairs %s (bottom %g mm, top %g mm) joins no two consecutive Levels."
                         % (stair["label"], stair["bottom"], stair["top"]))
    return found


def _within(inner, outer):
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def refusals(facts, index=library_index, unchanged=None):
    """What fails of the BIM Assertion, with the numbers, in the order the agent can mend them; empty where nothing fails.
    *index* answers the library's files, called only where Equipment is in the flat; *unchanged* is the signature of the Document an edit was given.
    A Document of two or more Levels also answers the multi-storey conditions.
    """
    found = []
    if facts["signature"] == unchanged:
        found.append("The Document is the one you were given. Make the change the intent asks for.")
    for kind, key in (("Wall", "walls"), ("Slab", "slabs"), ("Door", "doors"), ("Window", "windows"),
                      ("Space", "spaces")):
        if not facts[key]:
            found.append("The Document holds no %s. Add at least one." % kind)
    if not facts["buildings"]:
        found.append("The Document holds no Building. Group the Levels under Arch.makeBuilding().")
    for name in facts["unplaced"]:
        found.append("%s is not under a Building or Level. Add it to a Level's Group." % name)
    for wall in facts["walls"]:
        flat = [measure for measure in ("length", "height", "width") if wall[measure] <= 0]
        if flat:
            found.append("Wall %s has zero %s." % (wall["label"], " and ".join(flat)))
        elif not wall["volume"]:
            found.append("Wall %s has no solid; recompute and check its baseline." % wall["label"])
    for kind in ("doors", "windows"):
        for unhosted in (o for o in facts[kind] if not o["hosts"]):
            found.append("%s %s has no host Wall. Set its Hosts to the Wall it sits in."
                         % (kind[:-1].capitalize(), unhosted["label"]))
    if len(facts["levels"]) > 1:
        found += building_refusals(facts)
    entries = index() if facts["equipment"] else ()
    for item in facts["equipment"]:
        if not furniture.sourced(item["source"], entries):
            found.append("Equipment %s has Source %r, which is no file of the whitelisted library, \"%s\" or \"%s\"."
                         % (item["label"], item["source"], furniture.PARAMETRIC, furniture.GENERATED))
        if not item["level"]:
            found.append("Equipment %s is not under a Level. Add it to a Level's Group." % item["label"])
    ifc = facts["ifc"]
    if "error" in ifc:
        found.append("The Document does not export to IFC4: %s." % ifc["error"])
    else:
        if ifc["schema"] != "IFC4":
            found.append("The export is %s, not IFC4." % ifc["schema"])
        for kind, ifc_type in KINDS.items():
            if facts[kind.lower() + "s"] and not ifc[ifc_type]:
                found.append("The IFC4 export holds no %s although the Document holds a %s." % (ifc_type, kind))
        if facts["equipment"] and not ifc[FURNITURE]:
            found.append("The IFC4 export holds no %s although the Document holds Equipment." % FURNITURE)
    return found


def native_refusals(facts, unchanged=None, intent=""):
    """What fails of the Assertion over the saved IFC of an edit on native IFC, one line each; empty where nothing fails.
    *unchanged* is the signature of the IFC the edit was given and *intent* names what the edit removes; each Door and Window must fill an opening, each opening must have a filling, and each element must sit in a storey, whatever the given file held.
    """
    found = []
    held = {name for names in facts["labels"].values() for name in names}
    for name in sorted(set(arch.TARGET.findall(intent)) & held):
        found.append("%s is still in the IFC although the intent removes it. Remove its element with ifc_tools.remove_ifc_element." % name)
    for name in facts["orphans"]:
        found.append("The opening %s has no Door or Window filling it. Remove the opening, or fill it." % name)
    if facts["signature"] == unchanged:
        found.append("The IFC is the one you were given. Make the change the intent asks for.")
    for kind, count in facts["counts"].items():
        if not count:
            found.append("The IFC holds no %s. Add at least one." % kind)
    wanted = facts["counts"]["IfcDoor"] + facts["counts"]["IfcWindow"]
    if facts["openings"] < wanted:
        found.append("The IFC holds %d Doors and Windows but only %d openings in Walls. Give each its opening, voiding a Wall."
                     % (wanted, facts["openings"]))
    for name in facts["unfilled"]:
        found.append("%s fills no opening in a Wall. Fill an opening that voids its Wall." % name)
    for name in facts["unplaced"]:
        found.append("%s is not in a storey. Assign it to the Building Storey that holds its Wall." % name)
    if facts["schema"] != "IFC4":
        found.append("The IFC is %s, not IFC4." % facts["schema"])
    return found


def native_unfinished(kernel, unchanged=None, intent=""):
    """Why the nativeifc project is not a finished flat, one line per failing condition, or `None` where the Assertion holds.
    The IFC is read with ifcopenshell in the kernel because `harness/bim_freecad.py`, which reads the same relations to score, is outside `src` and may not be imported from it.
    """
    return domain_unfinished(kernel, NATIVE_FACTS_CHECK, lambda facts: native_refusals(facts, unchanged, intent), "BIM",
                             "The IFC could not be measured: the project would not save or reopen.")


def unfinished(kernel, unchanged=None):
    """Why the Document is not a finished flat, one line per failing condition, or `None` where the Assertion holds; *unchanged* is as in `refusals`."""
    return domain_unfinished(kernel, FACTS_CHECK, lambda facts: refusals(facts, library_index, unchanged), "BIM",
                             "The flat could not be measured: the Document would not recompute or export.")


def describe(facts):
    """One line naming what the Document holds, by the labels the intent speaks in."""
    hosted = lambda items: ", ".join("%s in %s" % (o["label"], " and ".join(o["hosts"]) or "no wall") for o in items)
    return ("The Document already holds this flat. Walls: %s. Doors: %s. Windows: %s. Spaces: %s. Not under a Building or Level: %s."
            % (", ".join(w["label"] for w in facts["walls"]), hosted(facts["doors"]) or "none",
               hosted(facts["windows"]) or "none", ", ".join(facts["spaces"]) or "none",
               ", ".join(facts["unplaced"]) or "nothing"))


def describe_native(facts):
    """One line naming what the IFC holds, by the labels the intent speaks in."""
    return "The Document holds this IFC as nativeifc objects. " + " ".join(
        "%s: %s." % (kind, ", ".join(labels) or "none") for kind, labels in facts["labels"].items())


def opening(intent, attachments, held=None):
    """The first message: the intent, what an edit's Document *held*, then the attachments."""
    blocks = [{"type": "text", "text": intent}] + ([{"type": "text", "text": held}] if held else [])
    return blocks + _attached(attachments)


def run_bim(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None, edit=False, native=False):
    """Build the flat *intent* and the floor plan in *attachments* describe; *edit* says the Document already holds the flat to change, and *native* that it holds it as the nativeifc project of an IFC."""
    system, held = read_prompt("bim.md"), None
    check = unfinished
    if edit and native:
        base = kernel.measure(NATIVE_FACTS_CHECK)
        system += "\n\n" + read_prompt("bim_edit.md") + "\n\n" + read_prompt("bim_edit_native.md")
        if base:
            held = describe_native(base)
            check = lambda running: native_unfinished(running, unchanged=base["signature"], intent=intent)
        else:
            check = lambda running: "The IFC you were given could not be measured: the project would not save or reopen."
    elif edit:
        base = kernel.measure(FACTS_CHECK)
        system += "\n\n" + read_prompt("bim_edit.md")
        if base:
            held = describe(base)
            check = lambda running: unfinished(running, unchanged=base["signature"])
        else:
            check = lambda running: "The Document you were given could not be measured: it would not recompute or export."
    domain = Domain(BIM, system, opening(intent, list(attachments), held), lambda: None, check,
                    furniture.calls(furniture.CACHE))
    return run_solo(intent, domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                    attachments=attachments, cancelled=cancelled, max_turns=MAX_TURNS)
