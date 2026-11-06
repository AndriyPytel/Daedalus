"""
A native IFC as nativeifc holds it: one IfcProject object whose IFC file the Document's objects edit in memory.
The kernel and the live Document each hold their own copy in memory: the live one is written to its `IfcFilePath` when the Document is copied, and the kernel's to a file of its own in its scratch folder at each checkpoint.
"""
import hashlib
import os
import shutil
from collections import Counter

def _tools():
    from nativeifc import ifc_tools

    return ifc_tools


def projects(document):
    """The IfcProject objects of *document*."""
    return [obj for obj in document.Objects if getattr(obj, "IfcClass", None) == "IfcProject"]


def name(obj):
    """The Name of the IFC element *obj* stands for, or its Label where it is no IFC object; Labels of two elements that share a Name differ by a numeric suffix."""
    element = _tools().get_ifc_element(obj) if is_native(obj) else None
    return element.Name if element is not None and element.Name else obj.Label


def open_ifc(document, ifc, work):
    """Open a copy of *ifc* at *work* in nativeifc inside *document*, with every object made."""
    from nativeifc import ifc_import

    shutil.copyfile(ifc, work)
    ifc_import.insert(work, document.Name, strategy=0, shapemode=2, switchwb=False, silent=True, singledoc=False)
    project = projects(document)[0]
    _tools().create_children(project, _tools().get_ifcfile(project), recursive=True, expand=True)
    document.recompute()


def save_projects(document, folder=None):
    """Write each project of *document* to its `IfcFilePath`, or, given a *folder*, to a file of its own there that the project then points to."""
    for project in projects(document):
        if folder is None:
            _tools().save_ifc(project)
        else:
            path = os.path.join(folder, project.Name + ".ifc")
            _tools().save_ifc(project, path)
            project.IfcFilePath = path


def export(document, path):
    """Write the project of *document* to *path* and hand the path back, or `None` where it holds none."""
    found = projects(document)
    if not found:
        return None
    _tools().save_ifc(found[0], path)
    return path


def is_native(obj):
    """Whether *obj* is an object of a nativeifc project."""
    return type(getattr(obj, "Proxy", None)).__module__.startswith("nativeifc")


def _loaded(document):
    return [project for project in projects(document) if hasattr(getattr(project, "Proxy", None), "ifcfile")]


def fingerprints(document):
    """A digest of the IFC each loaded project of *document* holds in memory, by object Name, for telling whether a Step changed it in this process."""
    return {project.Name: hashlib.sha1("\n".join(line for line in project.Proxy.ifcfile.to_string().splitlines()
                                                 if line.startswith("#")).encode()).hexdigest()
            for project in _loaded(document)}


def outlines(document):
    """A digest of what the IFC of each loaded project holds, by object Name, that two runs of the same code agree on: how many entities of each class, and each product's class, Name and plain attributes.
    Ids, GlobalIds and the order of references are left out, since the same code run twice differs in them.
    """
    found = {}
    for project in _loaded(document):
        ifcfile = project.Proxy.ifcfile
        counts = sorted(Counter(entity.is_a() for entity in ifcfile).items())
        products = sorted((entity.is_a(), repr(sorted(
            (key, round(value, 6) if isinstance(value, float) else value)
            for key, value in entity.get_info().items()
            if isinstance(value, (str, int, float, bool)) and key not in ("id", "type", "GlobalId"))))
            for entity in ifcfile.by_type("IfcRoot"))
        found[project.Name] = hashlib.sha1(repr((counts, products)).encode()).hexdigest()
    return found


def without_geometry():
    """Make nativeifc objects of this headless process build no shape, since IfcOpenShell's geometry iterator aborts it on some files, and say whether it did.
    A process with a GUI keeps its shapes: the user's Document is theirs to look at.
    """
    import FreeCAD
    from nativeifc import ifc_generator

    if FreeCAD.GuiUp:
        return False
    ifc_generator.generate_geometry = lambda obj, cached=False: None
    return True
