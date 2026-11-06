"""
The BIM Domain: the agent writes FreeCAD Python against the Arch workbench, and `finish` is refused until the Document holds one flat as native BIM objects.
That is at least one Wall, Slab, Door, Window and Space, every Door and Window hosted by a Wall, everything under a Building or Level, no Wall of zero length, height or width, and an export to IFC4 that ifcopenshell reopens with each of those kinds in it, and every piece of Equipment having an allowed Source and a Level.
The facts are measured in the kernel and judged here.
"""
from core import furniture
from core.agent.domain import BIM, Domain, measured as domain_measured, unfinished as domain_unfinished
from core.agent.solo import run_solo
from core.loop.prompt import _as_image
from core.prompts import read_prompt

MAX_TURNS = 80
FURNITURE = "IfcFurnishingElement"
KINDS = {"Wall": "IfcWall", "Slab": "IfcSlab", "Door": "IfcDoor", "Window": "IfcWindow", "Space": "IfcSpace"}

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
with open(FACTS_PATH, "w", encoding="utf-8") as out:
    json.dump(facts, out)
print("FACTS @")
""" % {"types": (*KINDS.values(), FURNITURE)}


def measured(kernel):
    """The flat's facts as the kernel leaves them, or `None` where the measuring itself failed."""
    return domain_measured(kernel, FACTS_CHECK)


def library_index():
    return furniture.index(furniture.CACHE, furniture.http_get)


def refusals(facts, index=library_index, unchanged=None):
    """What fails of the BIM Assertion, with the numbers, in the order the agent can mend them; empty where nothing fails.
    *index* answers the library's files, called only where Equipment is in the flat; *unchanged* is the signature of the Document an edit was given.
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


def opening(intent, attachments, held=None):
    """The first message: the intent, what an edit's Document *held*, then each floor plan, image or PDF, as a picture."""
    blocks = [{"type": "text", "text": intent}] + ([{"type": "text", "text": held}] if held else [])
    return blocks + [block for block in map(_as_image, attachments) if block]


def run_bim(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None, edit=False):
    """Build the flat *intent* and the floor plan in *attachments* describe; *edit* says the Document already holds the flat to change."""
    system, held, check = read_prompt("bim.md"), None, unfinished
    if edit:
        base = measured(kernel)
        system += "\n\n" + read_prompt("bim_edit.md")
        if base:
            held = describe(base)
            check = lambda running: unfinished(running, base["signature"])
        else:
            check = lambda running: "The Document you were given could not be measured: it would not recompute or export."
    domain = Domain(BIM, system, opening(intent, list(attachments), held), lambda: None, check,
                    furniture.calls(furniture.CACHE))
    return run_solo(intent, domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                    attachments=attachments, cancelled=cancelled, max_turns=MAX_TURNS)
