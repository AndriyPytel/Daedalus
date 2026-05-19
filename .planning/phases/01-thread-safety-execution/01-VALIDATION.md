# Validation Strategy: Phase 1

**Phase:** 1
**Updated:** 2026-04-08

---

## Nyquist Matrix

| Task Boundary | Feedback Signal (`<automated>`) | Latency | Coverage |
|---------------|-------------------------------|---------|----------|
| Remove vector logic from `rag_service.py` | `import freecad_copilot.core.rag_service` and run static type check | < 1s | Syntax |
| Rewrite `rags_service.py` with static loader | Execute static load, ensure memory buffer length > 10,000 | < 1s | Functional |
| Update `context_injector.py` to use full string | `pytest` wrapper executing `ContextInjector.get_developer_context()` | < 2s | Functional |
| Execute command on Main Qt thread | Verification inside FreeCAD Python console confirming transaction commits without GUI block | Man.  | Execute |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Existing infrastructure covers all phase requirements.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Transaction execution on Main Thread | EXEC-03 | Thread assertions require active Qt/FreeCAD mainloop | Start FreeCAD, open Copilot, enter prompt, verify execution happens without crash |
| Real-time character streaming | UX-01 | Streaming token visualization is purely visual PySide update | Watch Copilot UI during generation |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies 
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 5s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending 2026-04-08
