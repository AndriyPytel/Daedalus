# Phase 1: Thread Safety & Execution - Context

**Gathered:** 2026-04-08
**Status:** Ready for planning
**Source:** User Input

<domain>
## Phase Boundary

Fix RAG Hallucinations via Karpathy "Wiki/Full Context" Approach. Replace embedded RAG service with a "Full Context" approach that injects the entire API documentation straight into the Developer Agent's prompt to massively improve context cohesion and prevent hallucination/404 errors. Provide real-time token stream feedback with execution entirely off the main thread to prevent FreeCAD crashing.

</domain>

<decisions>
## Implementation Decisions

### RAG Service Deprecation
- Remove vector logic from `core/rag_service.py`
- Delete `_init_embeddings()`, `_chunk_text()`, `_load_or_build_cache()`, and `_build_cache()`.
- Remove `numpy`, `langchain_openai.OpenAIEmbeddings`, and `GoogleGenerativeAIEmbeddings` from the RAG Service completely.
- Keep the loaded full string strictly in memory for caching simplicity rather than persisting to JSON. 

### Context Injection Update
- Rewrite `core/rag_service.py` to statically load and return the entire `.md` corpus from `docs/api/`.
- Replace `search(query, top_k)` with `get_full_context()` that returns the full corpus as a single multiline string.
- Update `agents/context_injector.py` to use `get_full_context()` and bolt it onto the developer agent's context.

### Execution & UX Requirements (From Roadmap)
- Execute LLM network calls asynchronously so the FreeCAD GUI is never blocked (EXEC-01).
- Validate LLM-generated Python syntactically and semantically before permitting it to act (EXEC-02).
- Dispatch commands strictly on the PyQt Main Thread via custom `QThread` signals to avoid macOS UI thread execution crashes (EXEC-03).
- Provide a continuous token stream directly to the Copilot PySide panel (UX-01).

### the agent's Discretion
- Exact formatting and presentation of the "Full Context" injected into the prompt.
- Handling of FreeCAD internal transactions to satisfy EXEC-02/03 constraints gracefully.
- Specific exception handling for missing API docs.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Execution & UI
- `core/rag_service.py` — Target for removing embeddings and loading docs.
- `agents/context_injector.py` — Target for modifying how context is fed to the Developer Agent.

</canonical_refs>

<specifics>
## Specific Ideas

- The memory cache for strings is sufficient.
- The massive native context window of Gemini 1.5 Pro / Anthropic Sonnet will be utilized.

</specifics>

<deferred>
## Deferred Ideas

None — PRD covers phase scope

</deferred>

---

*Phase: 01-thread-safety-execution*
*Context gathered: 2026-04-08 via User Input*
