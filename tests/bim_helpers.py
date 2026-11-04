"""
Code a Level 2 test hands the kernel to build a one-room flat of native Arch objects, as an agent's `code` call would.
"""
FLAT = """
import Arch
import Draft
import Part

site = Arch.makeSite()
building = Arch.makeBuilding()
level = Arch.makeFloor()
corners = [App.Vector(0, 0, 0), App.Vector(4000, 0, 0), App.Vector(4000, 3000, 0),
           App.Vector(0, 3000, 0), App.Vector(0, 0, 0)]
walls = [Arch.makeWall(Draft.makeLine(a, b), width=200, height=2600, align="Center")
         for a, b in zip(corners, corners[1:])]
doc.recompute()


def opening(preset, kind, host, at, width, height):
    placement = App.Placement(at, App.Rotation(App.Vector(1, 0, 0), App.Vector(0, 0, 1),
                                               App.Vector(0, -1, 0), "ZXY"))
    obj = Arch.makeWindowPreset(preset, width, height, 50, 100, 100, 50, 50, 0, 0,
                                placement=placement)
    obj.IfcType = kind
    if host is not None:
        obj.Hosts = [host]
    return obj


window = opening("Open 1-pane", "Window", walls[0], App.Vector(1000, -100, 800), 900, 1200)
door = opening("Simple door", "Door", walls[2], App.Vector(1000, 2900, 0), 900, 2000)
slab = Arch.makeStructure(length=4000, width=3000, height=200)
slab.IfcType = "Slab"
slab.Placement.Base = App.Vector(2000, 1500, -100)
room = doc.addObject("Part::Feature", "Room")
room.Shape = Part.makeBox(3800, 2800, 2600, App.Vector(100, 100, 0))
space = Arch.makeSpace(room)
level.Group = walls + [window, door, slab, space]
building.Group = [level]
site.Group = [building]
doc.recompute()
"""

WITHOUT_SPACE = FLAT.replace("space = Arch.makeSpace(room)", "space = room")
UNHOSTED_WINDOW = FLAT.replace("Window\", walls[0], App", "Window\", None, App")
NO_BUILDING = FLAT.replace("building = Arch.makeBuilding()\n", "").replace(
    "building.Group = [level]\nsite.Group = [building]", "site.Group = [level]")
ZERO_HEIGHT_WALL = FLAT + "\nwalls[0].Height = 0\ndoc.recompute()\n"
STRAY_WALL = FLAT + """
stray = Arch.makeWall(Draft.makeLine(App.Vector(0, 5000, 0), App.Vector(3000, 5000, 0)),
                      width=200, height=2600)
doc.recompute()
"""
