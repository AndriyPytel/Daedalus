# Proposed Roadmap

**2 phases** | **7 requirements mapped** | All v1 requirements covered ✓

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Thread Safety & Execution | Run LLM interactions completely off the main thread while executing verified code safely. | EXEC-01, EXEC-02, EXEC-03, UX-01 | 3 |
| 2 | MEDA & Parametric Engine | Introduce the Multi-Expert Design Agent framework for constraint-aware sketch generation. | PARA-01, PARA-02, PARA-03 | 3 |


### Phase Details

**Phase 1: Thread Safety & Execution**
**Goal**: Run LLM interactions completely off the main thread while executing strictly verified Native UI code through explicit signal boundaries, giving users real-time stream feedback.
**Requirements**: EXEC-01, EXEC-02, EXEC-03, UX-01
**UI hint**: yes

**Success criteria**:
1. FreeCAD does not crash when the agent completes its generation loop.
2. Token generation is streamed to UI text boxes visibly character-by-character.
3. Executing python strictly evaluates in a wrapped document transaction.

**Phase 2: MEDA & Parametric Engine**
**Goal**: Introduce the Multi-Expert Design Agent framework for constraint-aware sketch generation and topology verification.
**Requirements**: PARA-01, PARA-02, PARA-03
**UI hint**: no

**Success criteria**:
1. Subagent constraints experts are instantiated when the user requests geometric bounds.
2. Fallback logic triggers cleanly if FreeCAD returns a parameter invalidation error.
3. Generation exclusively relies on native `PartDesign` tools over primitive objects.

---
