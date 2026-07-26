# Generated code may never contain a literal `Edge12` or `Face3`

FreeCAD 1.0 shipped the topological naming mitigation (PR #13705), which makes string references to elements less fragile than in 0.21 — but not stable. A hardcoded element name therefore fails silently: change a parameter upstream in the history and the fillet is still applied, just to the wrong edge. Silent wrong answers are worse than crashes, because nothing in the loop can detect them. Generated code must instead compute which elements it wants at execution time, or receive them from the user's live selection.

## Consequences

- An AST lint rejects literal `Edge<N>` / `Face<N>` / `Vertex<N>` before Preflight, and the assistant is told why. This replaces the hand-maintained substring checks the old validator accumulated one surprise at a time.
- Structurally stable constructs are preferred over element references wherever they exist — sketches attached via `AttachmentSupport` + `MapMode` to datum planes rather than positioned by a hardcoded `Placement`.
- FreeCAD's `setElementName` / ElementMap could give durable labels instead, but it is a second persistence layer to keep in sync across undo. Not taken.
