# Research: Phase 1 - Thread Safety & Execution

## Domain Investigation & Current State

### RAG Service (`core/rag_service.py`)
Currently, `RAGService` uses `langchain_openai.OpenAIEmbeddings`, `GoogleGenerativeAIEmbeddings` and `numpy` to build a vector store cached locally as `rag_cache.json`. It provides a `search(query, top_k)` method that computes cosine similarity. 
- The user's PRD requires removing all of this to avoid 404s from the embedding API.
- We must rewrite this class to load all `.md` files from `docs/api/` as a single multiline string.
- The state should be cached simply in memory (a class attribute) after the first evaluation.

### Context Injector (`agents/context_injector.py`)
Currently, `ContextInjector.get_developer_context(state)` fetches documents via `get_rag_service().search(query, top_k=2)`.
- It dynamically looks up a small subset of features.
- We must switch this out to grab the entire API documentation string using the new static loader from `RAGService`.
- Since context windows are massive, we will bolt the entire raw multiline text into the developer agent's context.

### Execution Engine (`core/command_executor.py`) [EXEC-02, EXEC-03]
Currently, PySide runs the AI tasks and streams via a QThread. `execute()` uses threading logic to detect if it is running on the main thread and attempts to dispatch execution to the main event loop using `FreeCADGui.addTodo` or `QTimer.singleShot(0, _run_on_main)`.
- Requirement **EXEC-03** mandates: "Dispatch document mutation commands strictly on the PyQt Main Thread via custom `QThread` signals to avoid macOS UI thread execution crashes."
- Currently `command_executor.py` uses `addTodo` and `QTimer`. According to the PRD, relying on explicit Qt Signals from a specifically designated `QObject` dispatcher might be intended to guarantee zero macOS issues.
- We need a reliable signal-based dispatcher mechanism or we leverage the existing worker architecture if it already satisfies the UI thread requirement. We must assure the `execute()` happens strictly via proper signals.

### Token Streaming (`ui/copilot_panel.py`) [EXEC-01, UX-01]
- The streaming pipeline appears somewhat functional already (e.g., `AiWorker`, `TokenEmitter`).
- Requirement **UX-01** specifies ensuring users can see the token stream character-by-character without UI freezing.
- Requirement **EXEC-01** executes LLM network calls asynchronously so GUI is unblocked. `AiWorker` correctly moves work to a QThread via `moveToThread`. The planner just needs to verify and enforce this architecture.

## Implementation Architecture

1. **Static RAG Loader:** Drop all `langchain_google_genai`, `langchain_openai`, and `numpy` overhead. RAGService simply returns `.read()` of all relevant Markdown files merged into a string.
2. **Context Injection:** `ContextInjector` unconditionally requests the full string instead of querying against vectors.
3. **Strict Executor Signals:** We may need to refactor `command_executor.py` to route execution requests strictly through a dedicated `QObject` signal/slot to ensure we aren't crossing thread boundaries dangerously on macOS.
4. **Asynchronous UX:** The AI pipeline (`system_controller.py`) should be confirmed to not block the main thread, and `TokenEmitter` properly bridges tokens to the UI view cleanly.

## Validation Architecture
- Verify FreeCAD starts and runs without crashes on `.get_developer_context()` invocation.
- Assert vector embedding API calls are completely removed (no networking except LLM prompt streaming).
- Confirm code execution strictly triggers on the main Qt thread.
