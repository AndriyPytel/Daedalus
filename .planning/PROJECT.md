# FreeCAD LLM Copilot

## What This Is

An add-on for FreeCAD that utilizes Large Language Models (LLMs) to automatically build and manipulate parametric 3D models. It orchestrates user intent into executable FreeCAD Python scripts using an agentic framework and custom PySide2 user interface widgets.

## Core Value

Translate human intent reliably and safely into executable FreeCAD parametric models, without crashing the native application.

## Requirements

### Validated

- ✓ PySide2 Dockable GUI integration within FreeCAD (CopilotWorkbench) — existing
- ✓ API LLM connectivity via `provider_manager.py` (Local/OpenAI/Anthropic) — existing
- ✓ Agentic architecture framework (`orchestrator`, `developer`, `validator`) — existing
- ✓ Basic thread-crossing mechanism for script execution in the GUI thread — existing

### Active

- [ ] Eliminate thread-safety crash loops caused when modifying the active FreeCAD document using QTimer/Gui.addTodo.
- [ ] Reduce LLM hallucinations involving deprecated FreeCAD API calls (Part primitives, Topology errors).
- [ ] Extend the parametric modeling abilities (e.g. chaining more complex operations, continuous multi-agent editing).
- [ ] Introduce stronger runtime validation or test automation for the agent execution engine.

### Out of Scope

- [Full Autonomous Offline Generation] — We rely on API LLMs first, offline is secondary to stabilizing the core architecture.
- [Replacing FreeCAD's Native Tools] — The goal is parametric code generation aid, not reinventing FreeCAD's C++ solver UI.

## Context

- **Environment**: FreeCAD (Native C++ core with Python exposed APIs), specifically PySide2 for all graphical interfaces.
- **Problem**: Building parametric models manually can be steep; using LLMs makes it accessible. However, executing arbitrary python inside FreeCAD from asynchronous threads causes segfaults.
- **Issues**: Known tech debt in `command_executor.py` regarding thread handoffs and missing robust integration tests.

## Constraints

- **Compatibility**: Code must run safely within FreeCAD's embedded Python interpreter.
- **Threading**: Absolute constraint that all document mutations `doc.recompute()` must occur strictly on the main thread.
- **Security**: Must evaluate AI-generated code safely without breaking the user's OS or exposing dangerous operations.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Retain PySide2 Native integration | Minimizes overhead and plays nicely with FreeCAD Workbenches vs an external IPC app. | — Pending |
| Use langchain for orchestration | Simplifies message templating and provider abstractions. | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: April 2026 after project initialization*
