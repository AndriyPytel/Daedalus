# v1 Requirements

## Execution & Security
- [x] **EXEC-01**: Execute LLM network calls asynchronously so the FreeCAD GUI is never blocked.
- [x] **EXEC-02**: Validate LLM-generated Python syntactically and semantically before permitting it to act.
- [x] **EXEC-03**: Dispatch document mutation commands strictly on the PyQt Main Thread via custom `QThread` signals to avoid macOS UI thread execution crashes.

## Parametric Modeling & MEDA (Multi-Expert Design)
- [ ] **PARA-01**: Implement the core MEDA (Multi-Expert Design Agent) framework, subdividing tasks between specialized sub-agents (e.g., a Constraints Expert, a Topology Expert) to ensure valid geometry.
- [ ] **PARA-02**: Establish fallback/retry logic specifically tuned to parse native FreeCAD parameter evaluation exceptions.
- [ ] **PARA-03**: Empower agents to correctly instantiate `PartDesign` Sketches and constraints natively rather than relying exclusively on simple `Part` primitives.

## User Experience
- [x] **UX-01**: Provide a continuous token stream directly to the Copilot PySide panel, allowing users to watch the agent "think" without UI freezing.

---

## v2 Requirements (Deferred)
- [ ] Continuous Autonomous Generative Loops without human intervention.
- [ ] Advanced dynamic GUI generation on the fly.

## Out of Scope
- **Replacing Native Solvers**: The Copilot modifies document parameters but defers all underlying math calculation layers to the native C++ solvers.
- **Support for non-PySide GUIs**: We are structurally committing to FreeCAD's internal Qt bindings.
