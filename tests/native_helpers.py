"""
A one-storey flat, or one without a storey or without fills, written as an IFC4 file whose Doors and Windows fill openings that void its Walls, for a Level 2 test to open in nativeifc.
"""
import ifcopenshell
import ifcopenshell.api


def flat(path, storeys=True, fills=True):
    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    run = lambda command, **kwargs: ifcopenshell.api.run(command, model, **kwargs)
    project = run("root.create_entity", ifc_class="IfcProject", name="Flat")
    run("unit.assign_unit")
    model_context = run("context.add_context", context_type="Model")
    body = run("context.add_context", context_type="Model", context_identifier="Body", target_view="MODEL_VIEW",
               parent=model_context)
    building = run("root.create_entity", ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", ifc_class="IfcBuildingStorey", name="Level") if storeys else building
    run("aggregate.assign_object", products=[building], relating_object=project)
    if storeys:
        run("aggregate.assign_object", products=[storey], relating_object=building)
    contained = []

    def make(ifc_class, name):
        element = run("root.create_entity", ifc_class=ifc_class, name=name)
        contained.append(element)
        if ifc_class == "IfcWindow":
            element.OverallWidth, element.OverallHeight = 0.9, 1.2
        return element

    walls = [make("IfcWall", "Wall_W%d" % number) for number in range(1, 5)]
    for wall in walls:
        wall.Representation = run("geometry.add_wall_representation", context=body, length=4.0, height=2.6, thickness=0.2)
    make("IfcSlab", "Slab")
    run("aggregate.assign_object", products=[run("root.create_entity", ifc_class="IfcSpace", name="Space_S1")],
        relating_object=storey)
    for ifc_class, name, wall in (("IfcDoor", "Door_D1", walls[0]), ("IfcWindow", "Window_WIN1", walls[1]),
                                  ("IfcWindow", "Window_WIN2", walls[2])):
        opening = run("root.create_entity", ifc_class="IfcOpeningElement", name=name)
        run("feature.add_feature", feature=opening, element=wall)
        filler = make(ifc_class, name)
        if fills:
            run("feature.add_filling", opening=opening, element=filler)
    run("spatial.assign_container", products=contained, relating_structure=storey)
    model.write(path)
    return path
