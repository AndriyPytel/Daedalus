# STRUCTURE

## Directory Layout

```
.
├── Init.py & InitGui.py       # Standard entry points for a FreeCAD add-on.
├── Mod/
│   ├── CopilotWorkbench.py    # Registers the visual workspace.
│   ├── CopilotPreferences.py  # User settings hook.
│   └── pages/                 # Preference configuration UI pages.
├── agents/
│   ├── orchestrator.py        # Central LLM routing logic.
│   ├── developer_agent.py     # Code generator agent.
│   ├── validator_agent.py     # Code compliance and safety checker.
│   └── ...                    # Other specialized sub-agents.
├── core/
│   ├── command_executor.py    # Safely evals generated scripts in FreeCAD.
│   ├── rag_service.py         # Knowledge retrieval for accurate API usage.
│   └── dependency_manager.py  # Ensures runtime pip dependencies.
├── ui/
│   ├── copilot_panel.py       # Main dockable user interface panel.
│   └── ...                    # Component widgets (chat, tabs, uploads).
├── utils/
│   ├── logger.py              # Telemetry and FreeCAD console logging.
│   ├── provider_manager.py    # LLM API key and model management.
│   └── settings_manager.py    # Wraps FreeCAD parameter reads/writes.
└── config/                    # Static configuration files (prompts, models).
```

## Key Entry Paths
- **Initialization**: `InitGui.py` runs on load, adds icon paths, checks dependencies, registers the preferences page, and finally registers the Workbench.
- **Agent Lifecycle**: A user interacting with the UI triggers the `Orchestrator`, which processes `history` and `context` and streams back actions.
