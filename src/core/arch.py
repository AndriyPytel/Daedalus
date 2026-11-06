"""
What the Arch workbench's objects are to Daedalus: which Document objects are Arch ones, and their IFC4 export.
"""
import re

ARCH_MODULE = re.compile(r"Arch[A-Z]\w*")


def arch_type(obj):
    """The Arch type name of *obj* (`Wall`, `Window`, `BuildingPart`...), or `None` where it is no Arch object."""
    proxy = getattr(obj, "Proxy", None)
    return getattr(proxy, "Type", None) if ARCH_MODULE.fullmatch(type(proxy).__module__) else None


TARGET = re.compile(r"\b(?i:remove|delete|replace)\b(?:(?!\.\s).)*?(?<![\w])([A-Z][a-z]*_\w*\w)")


def named_in(intent, document, names):
    """Whether *names* hold only the objects *intent* removes, deletes or replaces by Label, and what only they own.
    What a target owns is a non-Arch object below it that nothing else uses, such as its sketch; the Wall that hosts it is no such object.
    """
    objects = [o for o in map(document.getObject, names) if o is not None]
    targets = set(TARGET.findall(intent))
    named = [o for o in objects if o.Label in targets]
    owned = {id(o) for parent in named for o in parent.OutList
             if not arch_type(o) and all(user in named for user in o.InList)}
    return all(o in named or id(o) in owned for o in objects)


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
