"""
What the Arch workbench's objects are to Daedalus: which Document objects are Arch ones, and their IFC4 export.
"""
import re

ARCH_MODULE = re.compile(r"Arch[A-Z]\w*")


def arch_type(obj):
    """The Arch type name of *obj* (`Wall`, `Window`, `BuildingPart`...), or `None` where it is no Arch object."""
    proxy = getattr(obj, "Proxy", None)
    return getattr(proxy, "Type", None) if ARCH_MODULE.fullmatch(type(proxy).__module__) else None


def export_ifc(document, path):
    """Write *document*'s Arch objects to *path* as IFC4 and hand the path back, or `None` where it holds none."""
    import importers.exportIFC as exporter

    objects = [obj for obj in document.Objects if arch_type(obj)]
    if not objects:
        return None
    preferences = exporter.getPreferences()
    preferences["SCHEMA"] = "IFC4"
    exporter.export(objects, path, preferences=preferences)
    return path
