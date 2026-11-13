"""
What a run is asked to do: a Domain, an Intent, the files attached to it and the Document it starts from.

`Task.open` reads a task folder, a sheet or a bare Intent, with no FreeCAD and no Qt. The functions named `seed_*` build the Document from the seed, and only `run.py` calls them.
"""
import glob
import json
import os
import shutil
import tempfile
from collections import namedtuple

RENDER_VIEWS = ("front", "right", "top", "iso")
STEP = "step"
DOCUMENT = "document"
IFC = "ifc"

Seed = namedtuple("Seed", "kind path")


class Task(namedtuple("Task", "domain intent attachments seed saved_suffix")):
    """
    A task is a folder of one of the fixture layouts, or a sheet or no path at all with the *intent* to build from it.
    A folder carries its own Intent, so the *intent* given with it is not read, and *native* opens a BIM edit's IFC in nativeifc rather than its FCStd.
    `saved_suffix` is the file a run of the Task leaves: a STEP, an FCStd for an Assembly, an IFC4 file for BIM.
    """

    @classmethod
    def open(cls, path=None, intent=None, native=False):
        import sdk

        if path and os.path.isdir(path):
            if os.path.isfile(os.path.join(path, "task.json")):
                return _benchmark(path, native)
            return _edit(path)
        if not intent:
            raise ValueError("a sheet or a bare task needs an Intent")
        return cls(sdk.PART, intent, [path] if path else [], None, ".step")


def _edit(folder):
    import sdk

    with open(os.path.join(folder, "edit_description.txt"), encoding="utf-8") as handle:
        intent = sdk.read_prompt("edit_task_intent.md").strip().format(desc=handle.read().strip())
    renders = [os.path.join(folder, "renders", view + ".png") for view in RENDER_VIEWS]
    return Task(sdk.PART, intent, renders, Seed(STEP, os.path.join(folder, "input.step")), ".step")


def read_task(folder):
    """The parsed `task.json` of the benchmark task in *folder*."""
    with open(os.path.join(folder, "task.json"), encoding="utf-8") as handle:
        return json.load(handle)


def _benchmark(folder, native):
    import sdk

    task = read_task(folder)
    inputs, text = task["inputs"], task["text"]
    if "plan" in inputs or "input_ifc" in inputs:
        attachments = [os.path.join(folder, inputs["plan"])] if "plan" in inputs else []
        seed = None
        if "input_ifc" in inputs:
            seed = Seed(IFC, os.path.join(folder, "input.ifc")) if native else Seed(DOCUMENT, os.path.join(folder, "input.FCStd"))
        return Task(sdk.BIM, text, attachments, seed, ".ifc")
    if "input" in inputs:
        parts = sorted(glob.glob(os.path.join(folder, "parts", "*.step")))
        return Task(sdk.ASSEMBLY, text, parts, Seed(DOCUMENT, os.path.join(folder, "input.FCStd")), ".FCStd")
    if "graph" in inputs:
        return Task(sdk.ASSEMBLY, text + "\n\nThe graph:\n" + json.dumps(inputs["graph"], indent=1), [], None, ".FCStd")
    parts = [os.path.join(folder, part["file"]) for part in inputs["parts"]]
    return Task(sdk.ASSEMBLY, text, parts, None, ".FCStd")


def seed_from_step(step_path, keep=None):
    """
    A saved Document whose one Body has *step_path*'s solid as its BaseFeature, so an edit adds features to that Body and `export_step` writes the Body alone, never the imported solid beside it.

    The base is mended first when it is invalid, and the STEP it starts from, mended or as it came, is written to *keep*.
    """
    import FreeCAD
    import Import

    doc = FreeCAD.newDocument("Edit")
    Import.insert(step_path, doc.Name)
    base = next(obj for obj in doc.Objects
                if getattr(obj, "Shape", None) is not None and obj.Shape.Solids)
    import sdk

    shape = base.Shape
    repaired, note = sdk.repair_base(shape)
    if note:
        print("base repair   :", note)
    if keep:
        if repaired is shape:
            shutil.copyfile(step_path, keep)
        else:
            repaired.exportStep(keep)
            sdk.rewrite_step(repaired, keep)
    if repaired is not shape:
        base.Shape = repaired
    body = doc.addObject("PartDesign::Body", "Body")
    body.BaseFeature = base
    doc.recompute()
    handle, path = tempfile.mkstemp(prefix="fcc-edit-", suffix=".FCStd")
    os.close(handle)
    os.remove(path)
    doc.saveAs(path)
    FreeCAD.closeDocument(doc.Name)
    return path


def seed_document(task, keep=None):
    """The path of the Document a run of *task* opens, or `None` for a fresh one; *keep* is where a STEP seed's base is written."""
    if task.seed is None or task.seed.kind == DOCUMENT:
        return task.seed and task.seed.path
    if task.seed.kind == STEP:
        return seed_from_step(task.seed.path, keep)
    import sdk

    scratch = tempfile.mkdtemp(prefix="fcc-native-")
    return sdk.native_document(task.seed.path, os.path.join(scratch, "work.ifc"), os.path.join(scratch, "seed.FCStd"))
