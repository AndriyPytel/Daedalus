You are given a technical drawing and an intent. Reply with one complete Python script that builds the part as a PartDesign Body in a FreeCAD 1.0 Document, inside a single fenced ```python block and nothing else of consequence.

The script runs once, headless, with no second attempt and no feedback, so get it right the first time. `doc`, `App`, `Part`, `Sketcher` and `PartDesign` are in scope, and `doc` is an empty Document. Units are millimetres. Build one Body holding one solid, in the orientation and at the scale the drawing gives, and call `doc.recompute()` at the end. The part is exported as STEP exactly as the Document holds it.

## Known traps in this FreeCAD build

{{traps}}
