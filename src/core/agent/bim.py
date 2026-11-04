"""
The BIM Domain: the agent writes FreeCAD Python against the Arch workbench, and `finish` is refused until the Document holds one flat as native BIM objects.
That is at least one Wall, Slab, Door, Window and Space, every Door and Window hosted by a Wall, everything under a Building or Level, no Wall of zero length, height or width, and an export to IFC4 that ifcopenshell reopens with each of those kinds in it.
The facts are measured in the kernel, where the Document lives, and judged here.
"""
from core.agent.domain import BIM, Domain, measured as domain_measured, unfinished as domain_unfinished
from core.agent.solo import run_solo
from core.loop.prompt import _as_image
from core.prompts import read_prompt

MAX_TURNS = 80
KINDS = {"Wall": "IfcWall", "Slab": "IfcSlab", "Door": "IfcDoor", "Window": "IfcWindow", "Space": "IfcSpace"}

FACTS_CHECK = """
import json
import os
import tempfile

from core.arch import arch_type, export_ifc

PLACES = ("Building", "Building Storey")


def kind(obj):
    found = arch_type(obj)
    return "Slab" if found == "Structure" and obj.IfcType == "Slab" else found


def placed(obj):
    parents = [p for p in obj.InList if obj in (getattr(p, "Group", None) or ())]
    return any(arch_type(p) == "BuildingPart" and p.IfcType in PLACES or placed(p) for p in parents)


def unplaced(obj):
    if kind(obj) not in ("Wall", "Slab", "Window", "Space") or placed(obj):
        return False
    return not (kind(obj) == "Window" and any(placed(host) for host in obj.Hosts))


def hosts(obj):
    return [h.Label for h in obj.Hosts if arch_type(h) == "Wall"]


def length(wall):
    base = wall.Base
    if base is not None and getattr(base, "Shape", None) is not None and not base.Shape.isNull():
        return base.Shape.Length
    return wall.Length.Value


doc.recompute()
objects = [o for o in doc.Objects if arch_type(o)]
openings = [o for o in objects if kind(o) == "Window"]
facts = {
    "walls": [{"label": o.Label, "length": length(o), "height": o.Height.Value, "width": o.Width.Value,
               "volume": o.Shape.Volume if not o.Shape.isNull() else 0.0}
              for o in objects if kind(o) == "Wall"],
    "slabs": [o.Label for o in objects if kind(o) == "Slab"],
    "doors": [{"label": o.Label, "hosts": hosts(o)} for o in openings if o.IfcType == "Door"],
    "windows": [{"label": o.Label, "hosts": hosts(o)} for o in openings if o.IfcType != "Door"],
    "spaces": [o.Label for o in objects if kind(o) == "Space"],
    "buildings": len([o for o in objects if kind(o) == "BuildingPart" and o.IfcType == "Building"]),
    "unplaced": [o.Label for o in objects if unplaced(o)],
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
print("FACTS " + json.dumps(facts))
""" % {"types": tuple(KINDS.values())}


def measured(kernel):
    """The flat's facts as the kernel leaves them, or `None` where the measuring itself failed."""
    return domain_measured(kernel, FACTS_CHECK)


def refusals(facts):
    """What fails of the BIM Assertion, with the numbers, in the order the agent can mend them; empty where nothing fails."""
    found = []
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
    ifc = facts["ifc"]
    if "error" in ifc:
        found.append("The Document does not export to IFC4: %s." % ifc["error"])
    else:
        if ifc["schema"] != "IFC4":
            found.append("The export is %s, not IFC4." % ifc["schema"])
        for kind, ifc_type in KINDS.items():
            if facts[kind.lower() + "s"] and not ifc[ifc_type]:
                found.append("The IFC4 export holds no %s although the Document holds a %s." % (ifc_type, kind))
    return found


def unfinished(kernel):
    """Why the Document is not a finished flat, one line per failing condition, or `None` where the Assertion holds."""
    return domain_unfinished(kernel, FACTS_CHECK, refusals, "BIM",
                             "The flat could not be measured: the Document would not recompute or export.")


def opening(intent, attachments):
    """The first message: the intent, then each floor plan, image or PDF, as a picture."""
    blocks = [{"type": "text", "text": intent}]
    return blocks + [block for block in map(_as_image, attachments) if block]


def run_bim(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None):
    """Build the flat *intent* and the floor plan in *attachments* describe, under the BIM Domain."""
    domain = Domain(BIM, read_prompt("bim.md"), opening(intent, list(attachments)), lambda: None, unfinished)
    return run_solo(intent, domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                    attachments=attachments, cancelled=cancelled, max_turns=MAX_TURNS)
