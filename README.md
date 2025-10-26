# FreeCadCopilot

AI-powered command execution for FreeCAD with dynamic chat interface and safe transactional execution.

## Features

- 🤖 **AI Integration**: Chat with AI models (Google Gemini) using natural language
- 📝 **Dynamic Code Blocks**: Extract and execute Python code from AI responses
- ✅ **Transactional Execution**: Safe execution with FreeCAD transactions
- ↩️ **Granular Revert**: Undo individual code blocks with smart button management
- ⚙️ **Configurable**: Customizable system prompts and API settings
- 🎨 **Modern UI**: Beautiful, dockable chat interface using PySide2

## Installation

1. Clone this repository into your FreeCAD Mods directory:
```bash
cd ~/.FreeCAD/Mod
git clone <repository-url> FreeCadCopilot
```

2. Restart FreeCAD

3. Select the "FreeCad Copilot" workbench from the workbench dropdown

## Usage

1. Click the "⚙ Settings" button to configure:
   - API Key (for Google Gemini)
   - API Endpoint (for custom LLMs)
   - System Prompt (define how the AI should respond)

2. Type a command in the chat input (e.g., "Create a box")

3. The AI will respond with explanations and Python code blocks

4. Click "Execute" on any code block to run it

5. Click "Revert" to undo the most recent execution

## Project Structure

```
FreeCadCopilot/
├── Init.py                      # Module initialization
├── InitGui.py                   # GUI initialization
├── Mod/
│   ├── CopilotWorkbench.py     # Main workbench
├── ui/
│   ├── copilot_panel.py        # Main dockable panel
│   ├── chat_widgets.py         # Custom chat widgets
│   └── settings_dialog.py      # Settings dialog
├── core/
│   ├── command_executor.py     # Transactional code execution
│   ├── markdown_parser.py      # Markdown parsing logic
│   └── undo_manager.py         # Undo button state management
└── ai_handlers/
    ├── base_handler.py         # Abstract base handler
    └── gemini_handler.py       # Google Gemini implementation
```

## Dependencies

- FreeCAD (with PySide2/PyQt5)
- Python 3.x

## License

MIT License

## Contributing

Contributions welcome! Please read the contributing guidelines first.
