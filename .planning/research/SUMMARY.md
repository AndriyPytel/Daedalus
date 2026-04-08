# SUMMARY: FreeCAD Copilot Research

## Key Findings

**Stack**: To resolve the core issues, the stack must tightly integrate `Pydantic` for schema enforcement, and `PySide2` (`QThread`/`Signal`) for rigorous FreeCAD thread isolation.

**Table Stakes**: For a successful upgrade, it is mandatory to refactor code execution out of blocking event loops and ensure reliable FreeCAD Transaction control (wrapping executions safely). It is critical that LLMs target modern `PartDesign` schemas rather than archaic generic Parts. 

**Watch Out For**: LLM Topological hallucination (referencing missing faces) and aggressive MacOS application crashing if background threads mutate FreeCAD graphical environments.
