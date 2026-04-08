# CONCERNS

## Known Issues and Tech Debt
- **UI Thread Safety**: FreeCAD aggressively crashes if UI calls or Document transactions are made from a background thread. `command_executor.py` incorporates complex fallback logic (`QTimer.singleShot(0, ...)` and `addTodo`) to try and jump back to the main thread. Changes here require intense scrutiny to avoid segment faults.
- **Model Hallucinations Context Limits**: Models frequently hallucinate older APIs (like generic Part primitives). Maintaining the RAG indexing limits via `rag_service.py` is an ongoing maintenance concern when FreeCAD introduces new topological naming features.
- **Agent State Complexity**: Passing dictionaries between the orchestrator, validator, developer, and executing engine could become fragile without strong typed schemas (like Pydantic).
- **Test Automation**: A glaring lack of a robust PyTest suite means regressions inside core components are heavily dependent on manual QA.
