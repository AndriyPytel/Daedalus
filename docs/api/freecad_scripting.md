# FreeCAD 1.0 Python API Scripting Guide

## PartDesign Workbench and Bodies
When creating anything in PartDesign, you must first create a `PartDesign::Body` object and place all features inside it. Do NOT add PartDesign features directly to the document without a body.
```python
import FreeCAD as App
import PartDesign

doc = App.ActiveDocument
body = doc.addObject('PartDesign::Body', 'Body')
```

## Creating and Positioning Sketches
In FreeCAD 1.0, the `sketch.Support` and `sketch.Plane` properties are completely REMOVED. Do not use them.
If you need to place a sketch, set its `Placement` directly.
```python
import FreeCAD as App

sketch = doc.addObject('Sketcher::SketchObject', 'Sketch')

# ALWAYS use Placement to position sketches
sketch.Placement = App.Placement(App.Vector(0,0,0), App.Rotation(0,0,0,1))
```

If you are working inside a `PartDesign::Body`, simply create the sketch as a child of the body:
```python
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.Placement = App.Placement(App.Vector(0,0,0), App.Rotation(0,0,0,1))
```

## Sketcher Geometries and Constraints
When drawing geometries like Lines, Circles, or Rectangles inside a sketch, you must use `Part.LineSegment` or `Part.Circle`, and constrain them.
```python
import Part
import Sketcher

# Create a square
p1 = App.Vector(0, 0, 0)
p2 = App.Vector(10, 0, 0)
sketch.addGeometry(Part.LineSegment(p1, p2), False)

# Constrain the line to be horizontal
sketch.addConstraint(Sketcher.Constraint('Horizontal', 0))
```

## Extruding (Pad) and Pocketing
To extrude a sketch into 3D, use `PartDesign::Pad`.
```python
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 50.0  # Height of extrusion
```

## Document Attributes
You CANNOT dynamically assign arbitrary properties to `App.Document`.
For example, `doc.Length = 50` is FORBIDDEN and will cause an AttributeError crash.
If you need parametric variables, simply define them as standard Python variables at the top of your function:
```python
def create_model():
    length = 50.0
    width = 30.0
    
    sketch.addGeometry(Part.LineSegment(App.Vector(0,0,0), App.Vector(length, 0, 0)))
```
