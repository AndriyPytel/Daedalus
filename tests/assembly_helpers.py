"""
Code a Level 2 test hands the kernel to build a two-box Assembly, as an agent's `code` call would.
"""
PARTS = """
import JointObject, Part
assembly = doc.addObject('Assembly::AssemblyObject', 'Assembly')
assembly.Type = 'Assembly'
group = assembly.newObject('Assembly::JointGroup', 'Joints')


def box(name, at):
    part = doc.addObject('Part::Feature', name)
    part.Shape = Part.makeBox(10, 10, 10)
    part.Placement.Base = App.Vector(*at)
    assembly.addObject(part)
    return part


def ground(part):
    JointObject.GroundedJoint(group.newObject('App::FeaturePython', 'Ground'), part)


def fixed(one, other, offset):
    joint = group.newObject('App::FeaturePython', 'Joint')
    JointObject.Joint(joint, JointObject.JointTypes.index('Fixed'))
    joint.Reference1 = [one, ['', '']]
    joint.Reference2 = [other, ['', '']]
    joint.Offset1 = App.Placement(App.Vector(*offset), App.Rotation())
    joint.Offset2 = App.Placement()


a = box('A', (0, 0, 0))
b = box('B', (30, 0, 0))
doc.recompute()
"""

SOLVED = PARTS + """
ground(a)
fixed(a, b, (10, 0, 0))
doc.recompute()
"""
