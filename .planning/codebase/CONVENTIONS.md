# CONVENTIONS

## Coding Standards
- **Python Conventions**: Adheres to standard PEP 8 naming conventions. Usually functions and variables use `snake_case`, while classes use `PascalCase`.
- **Imports**: PySide2 imports are strictly used over PyQt5 for explicit compatibility with modern FreeCAD Qt architectures.

## Agent System Patterns
- **Role Isolation**: Each AI agent has a strict `role` and defined `prompt_template`.
- **State Management**: Agent `invoke()` methods always accept a `state: dict` and must return a modified or appended state dictionary, adhering to generic LangGraph node patterns.
- **Routing Guidelines**: The Orchestrator strictly yields `> **NEXT_AGENT:**` labels to delegate flow, keeping control chains observable.

## UI Styling
- Styles are typically applied via Qt stylesheets. The plugin adapts its styling to interface gracefully via FreeCAD's native workbench theme.

## Error Handling
- Safe execution logic inside `command_executor.py` explicitly wraps generated instructions in `try/except` and utilizes `doc.abortTransaction()` heavily to prevent model hallucinations from corrupting active projects.
