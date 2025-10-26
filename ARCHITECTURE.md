# Architecture Overview

## File Structure

```
FreeCadCopilot/
├── Init.py                      # Module initialization
├── InitGui.py                   # GUI initialization (registers workbench)
├── Mod/
│   ├── __init__.py
│   └── CopilotWorkbench.py     # Main workbench class
├── ui/
│   ├── __init__.py
│   ├── copilot_panel.py        # Main QDockWidget panel
│   ├── chat_widgets.py         # TextMessage, CodeBlockWidget
│   └── settings_dialog.py      # Settings dialog
├── core/
│   ├── __init__.py
│   ├── command_executor.py     # Transactional code execution
│   ├── markdown_parser.py      # Extract code blocks from markdown
│   └── undo_manager.py         # Manage revert button stack
└── ai_handlers/
    ├── __init__.py
    ├── base_handler.py         # Abstract base class
    └── gemini_handler.py       # Gemini implementation
```

## Key Components

### 1. Workbench (Mod/CopilotWorkbench.py)
- Initializes when FreeCAD workbench is activated
- Creates and manages the CopilotPanel
- Registers the dockable widget

### 2. UI Panel (ui/copilot_panel.py)
- Main chat interface using QListWidget
- Manages TextMessage and CodeBlockWidget instances
- Handles user input and AI responses
- Connects execute/revert signals

### 3. Chat Widgets (ui/chat_widgets.py)
- **TextMessage**: Displays user/AI text with different styling
- **CodeBlockWidget**: Shows code with Execute/Revert buttons
- Signals: `execute_requested(str)`, `revert_requested()`

### 4. Settings (ui/settings_dialog.py)
- Model selection, API key, endpoint, system prompt
- Settings persistence (TODO: implement with FreeCAD.ParamGet)

### 5. Command Executor (core/command_executor.py)
- Wraps code in FreeCAD transactions
- Uses `doc.openTransaction()`, `exec()`, `doc.commitTransaction()`
- Reverts with `doc.undo()`

### 6. Undo Manager (core/undo_manager.py)
- Manages stack of executed widgets
- Only top widget's revert button is enabled
- Follows FreeCAD's undo stack order

### 7. Markdown Parser (core/markdown_parser.py)
- Extracts ```python ... ``` blocks
- Returns list of code strings

### 8. AI Handlers (ai_handlers/)
- **BaseHandler**: Abstract interface
- **GeminiHandler**: Google Gemini API (placeholder for now)

## Data Flow

1. User types message → CopilotPanel
2. CopilotPanel → AI Handler → Markdown response
3. Markdown Parser extracts code blocks
4. For each code block: create CodeBlockWidget
5. User clicks Execute → CommandExecutor
6. On success: show Revert button, add to UndoManager stack
7. User clicks Revert → undo(), pop from stack

## Transaction Safety

Each code block execution:
1. Opens new transaction: `doc.openTransaction("FreeCadCopilot")`
2. Executes code with try/except
3. On success: commit transaction
4. On error: abort transaction, show error

## Revert Button Management (FR7)

Only the most recent execution's Revert button is enabled.
When reverted:
1. Call `doc.undo()`
2. Pop widget from UndoManager stack
3. Hide revert button, show execute button
4. Enable the new top widget's revert button

## Next Integration Steps

1. Wire CommandExecutor to CopilotPanel._on_execute_code
2. Wire UndoManager to handle revert
3. Implement async AI calls with QThread
4. Add settings persistence
5. Add error display in chat
