"""
Equipment in the Document: an Arch.Equipment on a Level that records where it came from (`Source`), its licence, title and author, so the Assertion can judge it and the IFC4 export can be attributed.
A library item is the shape of a FreeCAD file standing on the Level at its place; a parametric item is a box of the kind's typical size.
"""
import os

from core import furniture
from core.arch import arch_type

PROVENANCE = ("Source", "Licence", "LicenceURL", "Title", "Author")


def _level(document, label):
    found = [o for o in document.getObjectsByLabel(label) if arch_type(o) == "BuildingPart"]
    if not found:
        raise ValueError("no Level is labelled %r" % label)
    return found[0]


def equip(document, shape, level, spot, provenance):
    """Stand *shape*, its footprint centred on *spot* (x, y, rotation) and its underside on the Level labelled *level*, as Equipment named and attributed by *provenance*."""
    import Arch
    import FreeCAD as App

    floor = _level(document, level)
    x, y, rotation = spot
    box = shape.BoundBox
    shape = shape.copy()
    shape.translate(App.Vector(-box.Center.x, -box.Center.y, -box.ZMin))
    base = document.addObject("Part::Feature", provenance["Title"].replace(" ", "_") + "_shape")
    base.Shape = shape
    obj = Arch.makeEquipment(base)
    obj.Label = provenance["Title"]
    obj.IfcType = "Furniture"
    for name in PROVENANCE:
        obj.addProperty("App::PropertyString", name, "Provenance")
        setattr(obj, name, provenance[name])
    obj.Placement = App.Placement(App.Vector(x, y, floor.Placement.Base.z), App.Rotation(App.Vector(0, 0, 1), rotation))
    floor.Group = list(floor.Group) + [obj]
    return obj


def place_library(document, path, provenance, x, y, rotation, level):
    """Place the shape of the FreeCAD file at *path* as Equipment attributed by *provenance*."""
    import FreeCAD as App
    import Part

    opened = App.openDocument(path)
    try:
        solids = [o.Shape for o in opened.Objects
                  if not o.InList and getattr(o, "Shape", None) is not None and not o.Shape.isNull() and o.Shape.Solids]
        shape = Part.Compound(solids) if len(solids) > 1 else solids[0]
        shape = shape.copy()
    finally:
        App.closeDocument(opened.Name)
        App.setActiveDocument(document.Name)
    return equip(document, shape, level, (x, y, rotation), provenance)


def place_parametric(document, kind, x, y, rotation, level):
    """Place a box of *kind*'s typical size as Equipment whose Source is `parametric`."""
    import Part

    name = kind.capitalize()
    provenance = dict.fromkeys(PROVENANCE, "") | {"Title": name, "Source": furniture.PARAMETRIC}
    return equip(document, Part.makeBox(*furniture.SIZES[kind]), level, (x, y, rotation), provenance)


def library_items(document, entries):
    """The attribution entries of the Equipment whose Source is a file of the library's *entries*."""
    return [{"title": o.Title, "author": o.Author, "licence": o.Licence, "licence_url": o.LicenceURL, "url": o.Source}
            for o in document.Objects
            if arch_type(o) == "Equipment" and hasattr(o, "Source") and furniture.short(o.Source) in entries]


def write_attribution(document, ifc_path, entries):
    """Write `<ifc stem>.ATTRIBUTION.md` beside *ifc_path* where the Document holds library items, and hand its path back, else `None`."""
    items = library_items(document, entries)
    if not items:
        return None
    path = os.path.splitext(os.path.abspath(ifc_path))[0] + ".ATTRIBUTION.md"
    with open(path, "w") as out:
        out.write(furniture.attribution(items))
    return path
