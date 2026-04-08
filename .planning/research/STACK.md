# STACK: FreeCAD LLM Addon Extensions

## Core Additions
When extending an LLM-based FreeCAD copilot with Thread Safety and Parametric fixes, the standard additions to the codebase include:

- **PySide2 Threading Framework**: Explicitly using `QThread`, `QtCore.Signal`, and `QtCore.Slot`. Relying exclusively on signals to bridge background LLM execution to main-thread document operations.
- **Pydantic**: Critical for enforcing schema constraints on the Langchain JSON output, stopping "key error" hallucinations dead in their tracks.
- **FreeCAD TopoData**: Directly accessing standard Topological Naming methods (e.g. `doc.getObject("Body").Shape`) to parse the active 3D context prior to model generation.

## Avoid
- **Raw `threading.Thread`**: Mixing standard python threads to mutate PySide components will instantly segfault FreeCAD.
- **Legacy FreeCAD API**: Avoid `Part.makeBox` type primitives; LLMs should exclusively generate `PartDesign` driven workflows for robust paramaterization.
