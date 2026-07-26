# API knowledge comes from introspecting the running FreeCAD, not from retrieval over the docs

The recurring failure mode is generated code calling FreeCAD API that no longer exists — `FreeCADGui.addTodo`, `sketch.Support`, `sketch.Plane`. The obvious fix is RAG over the FreeCAD wiki and forum, and it is the wrong one: that corpus is mixed across versions and full of 0.21-era examples using exactly the removed API, so retrieval would inject the hallucinations it was meant to cure, wearing the authority of a citation. Instead the assistant asks the running FreeCAD — `PropertiesList`, `dir`, `supportedTypes` — which is authoritative for the user's exact build.

## Consequences

- A typo like `Lenght` dies against the real property list, with the correct name in the same answer.
- Introspection reports what exists, not what is correct, so a short list of known traps stays in the prompt — attaching sketches goes through `AttachmentSupport` + `MapMode`, not `Placement`. A list of traps, not a dump of documentation.
- Preflight tracebacks are the other knowledge source, and the more important one: they are exact, version-correct and free.
- `freecad-stubs` with a type checker was considered as a fast pre-filter. Deferred: FreeCAD's objects acquire their properties at runtime, which is precisely where the stubs are blind and the introspection is not.
