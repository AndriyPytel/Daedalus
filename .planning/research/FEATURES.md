# FEATURES: FreeCAD LLM Addon Extensions

## 1. Thread-Safe Execution Engine
**Table Stakes:**
- LLM network calls run asynchronously off the GUI thread
- Generated Python is verified in a strict transaction
- Document mutations (`exec()`, `doc.recompute()`) are passed back to main thread safely via Signals.

**Differentiators:**
- UI Progress Stream: See generating output character-by-character without freezing FreeCAD.

## 2. Parametric Modeling Extension
**Table Stakes:**
- Fallback logic when FreeCAD natively rejects invalid topology paths
- Support for Sketcher generation and constraint mapping

**Differentiators:**
- Agent-to-Agent feedback loops verifying boundary selections before committing FreeCAD transaction.

## Anti-Features
- **Full Autonomous Drive**: The extension should not generate massive assemblies without intermediate human-in-the-loop validation milestones.
