# STACK

## Core Languages
- **Python 3.x**: Primary language for the FreeCAD add-on and agent logic.

## Platforms & Frameworks
- **FreeCAD API**: Native C++ API bindings, accessed via `FreeCAD` (`App`) and `FreeCADGui` (`Gui`) modules.
- **PySide2**: Qt bindings used for all UI components within the FreeCAD Copilot panel.
- **LangChain**: Used for defining and orchestrating the AI agents (`langchain_core` for prompts and messages).

## Infrastructure & Dependencies
- **LLM Integrations**: External model providers (OpenAI, Anthropic, etc.) managed via `provider_manager.py`.
- **RAG & Vector Search**: Functionality provided via `rag_service.py` to retrieve relevant FreeCAD documentation.

## Package Management
- Handled dynamically via `dependency_manager.py` (checks/installs `langchain`, etc. on workbench initialization).
