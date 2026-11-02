"""
Runs inside freecadcmd, never the harness venv: DAE_JOB names a JSON file with "build" (a list of
[spec, path] pairs written as solved-in-place Assembly documents) and "measure" (a list of
documents to solve and measure); the measurements go to DAE_RESULT. A spec lists parts (a STEP or
a box in mm, with an x y z qx qy qz qw placement), joints with a frame on each part, and the
grounded part.
"""
import itertools
import json
import os

import FreeCAD as App
import JointObject
import Part

JOINT_TYPES = JointObject.JointTypes


def pose(values):
    return App.Placement(App.Vector(*values[:3]), App.Rotation(*values[3:]))


def make_part(doc, part):
    obj = doc.addObject("Part::Feature", part["label"])
    if "step" in part:
        obj.Shape = Part.read(part["step"])
    else:
        obj.Shape = Part.makeBox(*part["box"])
    obj.Label = part["label"]
    obj.Placement = pose(part["placement"])
    return obj


def build(spec, path):
    doc = App.newDocument("Assembly")
    assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
    assembly.Type = "Assembly"
    group = assembly.newObject("Assembly::JointGroup", "Joints")
    parts = {part["label"]: make_part(doc, part) for part in spec["parts"]}
    for obj in parts.values():
        assembly.addObject(obj)
    doc.recompute()
    if spec["ground"]:
        JointObject.GroundedJoint(group.newObject("App::FeaturePython", "Ground"), parts[spec["ground"]])
    for joint in spec["joints"]:
        obj = group.newObject("App::FeaturePython", "Joint")
        JointObject.Joint(obj, JOINT_TYPES.index(joint["type"]))
        obj.Reference1 = [parts[joint["a"]], ["", ""]]
        obj.Reference2 = [parts[joint["b"]], ["", ""]]
        obj.Offset1 = pose(joint["frame_a"])
        obj.Offset2 = pose(joint["frame_b"])
    doc.recompute()
    doc.saveAs(path)
    App.closeDocument(doc.Name)


def measure(path):
    doc = App.openDocument(path)
    doc.recompute()
    assembly = next(o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject")
    status = assembly.solve()
    parts = {o.Label: o for o in assembly.Group if hasattr(o, "Shape")}
    grounded = next((o.ObjectToGround for o in doc.Objects if hasattr(o, "ObjectToGround") and o.ObjectToGround), None)
    anchor = (grounded or next(iter(parts.values()))).Placement.Rotation.inverted()
    result = {"solve": status, "parts": {}, "joints": [], "interference": 0.0}
    for label, obj in parts.items():
        box = obj.Shape.BoundBox
        result["parts"][label] = {
            "bbox": [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax],
            "com": list(obj.Shape.CenterOfMass),
            "volume": obj.Shape.Volume,
            "rot": list((anchor * obj.Placement.Rotation).Q),
            "faces": len(obj.Shape.Faces),
        }
    for obj in doc.Objects:
        if hasattr(obj, "JointType") and hasattr(obj, "Reference1"):
            result["joints"].append({"type": obj.JointType, "a": obj.Reference1[0].Label, "b": obj.Reference2[0].Label})
    for (_, one), (_, other) in itertools.combinations(parts.items(), 2):
        if one.Shape.BoundBox.intersect(other.Shape.BoundBox):
            result["interference"] += max(0.0, one.Shape.common(other.Shape).Volume)
    result["diagonal"] = sum(
        (max(p["bbox"][i + 3] for p in result["parts"].values()) - min(p["bbox"][i] for p in result["parts"].values())) ** 2
        for i in range(3)
    ) ** 0.5
    App.closeDocument(doc.Name)
    return result


with open(os.environ["DAE_JOB"]) as source:
    job = json.load(source)
for spec, path in job.get("build", []):
    build(spec, path)
with open(os.environ["DAE_RESULT"], "w") as out:
    json.dump([measure(path) for path in job.get("measure", [])], out)
