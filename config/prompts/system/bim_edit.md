## Editing

The Document already holds the flat, and the intent asks for a change to it, not for a new one. Read what it holds from the line after the intent, find the objects by those labels, and change only what the intent names: every other object keeps its label, shape and place. Do not rebuild the flat.

The result still has to meet everything above, and `finish` is refused until the Document also differs from the one you were given. If the line says an object is not under a Building or Level, put it there with the rest of your change.

- Add a door or window: make it with `Arch.makeWindowPreset` at the size and place the intent gives, set its `Hosts` to the Wall it sits in, set `IfcType` to `Door` for a door, and put it in the Group of the Level that holds the Wall.
- Remove a door or window: delete that object and the sketch under its `Base`, nothing else; the Wall closes the opening when it recomputes.
- Resize a window: change its `Width` and `Height`, keep its place on the Wall and its host.
- Move a wall: move its `Base` line by the vector given, then move every door and window it hosts by the same vector, then reshape the Spaces on either side so their outline still meets the wall's faces.
- Split a room: add the new Wall with the thickness and ends the intent gives, keep the Space and give the new one the same kind, and reshape both outlines so each ends at a face of the new Wall.

Delete only the object the intent removes or replaces, and what only it owns; no other deletion is approved.
