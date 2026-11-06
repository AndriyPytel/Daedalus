## Native IFC

The Document is not Arch objects: it is an IFC opened in nativeifc. One `IfcProject` object holds the IFC file, and every Wall, Slab, Door, Window and Space is an object with an `IfcClass` and a `Label`. Ignore the Arch rules above for making objects; change the IFC itself.

- The line after the intent names elements by their IFC Name. Find an object by `ifc_tools.get_ifc_element(obj).Name` and `IfcClass`, not by `Label`: two elements that share a Name get Labels that differ by a numeric suffix.
- An object's IFC attributes are its properties in the `IFC` group (for a window `OverallWidth`, `OverallHeight`); set them with `App.Units.Quantity`.
- Change an element with `ifcopenshell.api` through `nativeifc.ifc_tools.api_run(command, ifc_tools.get_ifcfile(project), ...)`; `ifc_tools.get_ifc_element(obj)` is the element. Setting an object's `Placement` edits the element's IFC placement (FreeCAD in mm, the file in its own unit). A size is in the element's representation: change its `IfcExtrudedAreaSolid` and profile directly.
- Remove an element with `ifc_tools.remove_ifc_element(obj, delete_obj=True)`. A Door or Window sits in an `IfcOpeningElement` object that has the same Name: remove the Door or Window first and then that opening, when the intent closes it, and no other object: removing the opening first deletes the Door's or Window's object but leaves its element in the IFC.
- `finish` is refused while an element the intent removes is still in the IFC, and while an opening has no Door or Window filling it.
- Do not read geometry from an object's `Shape`: in a headless run the objects carry none. Read sizes and places from the IFC with ifcopenshell, from the element's `ObjectPlacement` and the items of its `Representation`.
- You do not save or export: the run saves the project's IFC when you `finish`, and the Assertion reads that IFC.
- The result must differ from the IFC you were given, hold Wall, Slab, Door, Window and Space, keep every Door and Window filling an opening in a Wall, keep every element in its storey where the file has storeys, and stay IFC4.
