# ARCHITECTURE: FreeCAD LLM Addon Extensions

## Component Boundaries
Adding extensions requires clear boundary separations:
1. **Network Boundary**: Agent LLM requests managed in a purely disconnected Python `QThreadWorker`.
2. **Execution Boundary**: `command_executor.py` acts strictly on the receiving end of Qt Signals. 
3. **Context Boundary**: RAG modules observe the active `FreeCAD.App` Document.

## Data Flow
User Input -> UI Controller -> QThreadWorker(Orchestrator -> Developer) -> Developer Yields Script -> UI Controller -> Execution Engine (Main Thread) -> FreeCAD Native Tree.

## Build Order
1. Stabilize thread isolation (Signals/Slots replacing `addTodo`).
2. Beef up Validator Agent to catch missing FreeCAD dependencies.
3. Augment parametric capabilities in Developer instructions.
