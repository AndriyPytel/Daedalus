# `@assistant-ui/react` (assistant-ui): what it is, and what we would actually get

Research note, read-only. Every fact below is from a primary source — the library's own docs
in `assistant-ui/assistant-ui@main`, the published npm/PyPI metadata, the shadcn registry at
`r.assistant-ui.com`, and the GitHub/npm APIs. Figures fetched **2026-08-17**; the versions
current that day were `@assistant-ui/react@0.15.14` (2026-08-12) and `assistant-stream@0.0.36`
(PyPI, 2026-08-04). URLs in [Sources](#sources).

Scope: the library only. FreeCAD hosting is out of scope by instruction.

---

## Executive summary

1. **It is a React library, not a framework and not an app** — headless primitives plus a runtime
   layer that owns messages, threads, branching and the run lifecycle, plus a shadcn-style registry
   of styled components you *copy into your source tree*. MIT, 11,696 stars, 1,446,383 npm
   downloads in the week ending 2026-08-15.
2. **The runtime that lets a Python process own the state exists and is first-party.**
   `AssistantTransport` (layered on `ExternalStoreRuntime`) streams *agent-state snapshots* from
   your backend; the matching encoder ships as the **PyPI package `assistant-stream`** (MIT,
   `>=3.10`), and the repo carries two runnable Python backends (`python/assistant-transport-backend`,
   `python/assistant-transport-backend-langgraph`). Every backend snippet in that doc page is Python.
3. **Four of our five requirements are shipped, one is not.** Collapsible streaming reasoning,
   attachments, per-session history/threads and an approval gate that pauses the run all exist.
   The plan/checklist widget exists only as **pixels** (`elements-agent-plan`), unwired to the runtime.
4. **The cloud is optional and separable.** The pricing page states the library is "Forever free &
   open source / MIT License / Bring your own backend". No account is needed. But `assistant-cloud`
   is a *hard npm dependency* of `@assistant-ui/react` (inert unless you construct it).
5. **The biggest real constraint is not licensing or Tailwind — it is the stability contract.**
   Only *Primitives* are "stable"; the Runtime API, message types, styled components and attachment
   APIs are "Beta" with a **<1 month** deprecation window, and several APIs we would depend on carry
   the `unstable_` prefix that the project says "may change in any release including patch releases".

---

## 1. What it is

### 1.1 Shape

Not a component kit, not a framework — **a runtime + a primitive set + a registry**, in three
distinguishable pieces:

| Piece | Delivered as | Example |
|---|---|---|
| Runtime | npm package, React context | `useExternalStoreRuntime`, `AssistantRuntimeProvider` |
| Primitives (headless, unstyled) | npm package | `ThreadPrimitive.Root/.Viewport/.Messages`, `MessagePrimitive.GroupedParts`, `ComposerPrimitive.Send/.Cancel`, `BranchPickerPrimitive` |
| Styled components | **copied into your repo** via a shadcn registry | `npx shadcn add https://r.assistant-ui.com/thread.json` → writes `components/assistant-ui/thread.tsx` |

The styled layer is not a dependency you upgrade. `thread.json` is a **22,596-character `.tsx` file**
that lands in your tree and is yours from then on. That is a feature (total control) and a cost
(no upstream fixes).

The monorepo publishes ~47 packages (`packages/`), including `react-native`, `react-ink` (terminal),
`vue`, `svelte`, `react-markdown`, `react-syntax-highlighter`, `react-ag-ui`, `react-langgraph`.

### 1.2 `AssistantRuntimeProvider`

One React context that publishes a single runtime instance to every primitive below it. Components
never receive messages as props — `<Thread />`, `<ThreadList />`, the composer and the branch picker
all read the context. Swapping backends means swapping the hook that builds `runtime`; the tree
underneath is untouched.

```tsx
const runtime = useExternalStoreRuntime({ messages, isRunning, onNew });

return (
  <AssistantRuntimeProvider runtime={runtime}>
    <Thread />
  </AssistantRuntimeProvider>
);
```

### 1.3 The runtime layers

Three layers, each implemented in terms of the one below (`docs/runtimes/concepts/architecture`):

```text
framework adapters   react-ai-sdk, react-langgraph, react-langchain, react-google-adk,
                     react-a2a, react-ag-ui, react-opencode
protocol layers      DataStream ──► LocalRuntime
                     AssistantTransport ──► ExternalStoreRuntime
core runtimes        LocalRuntime          ExternalStoreRuntime
```

| Runtime | Who owns message state | You implement | Fit for a Python-owned copilot |
|---|---|---|---|
| `useChatRuntime` (`@assistant-ui/react-ai-sdk`) | AI SDK client + your HTTP route | a Vercel-AI-SDK-shaped endpoint | poor — assumes the AI SDK wire format and a JS server idiom |
| `useLocalRuntime(adapter)` | **the runtime**, in the browser | one `run` / `async *run` method | workable; assistant-ui holds the thread, Python is a stream source |
| `useExternalStoreRuntime(store)` | **you** | `messages`, `isRunning`, `onNew`, plus optional callbacks | **direct fit** — the array is yours, any transport |
| `useAssistantTransportRuntime(...)` | **your backend** | an endpoint that returns a state-snapshot stream | **the designed fit** — has a first-party Python library |
| `useDataStreamRuntime` | the runtime (Local) | a backend emitting the data-stream protocol | message-level only, no agent state |

`ChatModelAdapter` is the `LocalRuntime` seam, not a runtime itself:

```ts
run(options: ChatModelRunOptions) => Promise<ChatModelRunResult> | AsyncGenerator<ChatModelRunResult, void>
```

### 1.4 The runtime for an external (Python) owner: `AssistantTransport`

> "Instead of streaming message parts, your backend streams snapshots of its full agent state and
> the runtime converts them into UI messages." — `docs/runtimes/custom/assistant-transport`

Direction of flow: frontend → **commands**, backend → **state snapshots**. Built-in commands are
`add-message` and `add-tool-result`; you may declare your own. The wire carries two operations only,
`set` and `append-text`, addressed by JSON path:

```text
aui-state:[{"type":"set","path":["status"],"value":"completed"}]
aui-state:[{"type":"append-text","path":["message"],"value":" World"}]
```

**Python side** (this is the shape the official docs use, verbatim in style):

```python
from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse

@app.post("/assistant")
async def chat_endpoint(request: ChatRequest):
    async def run_callback(controller: RunController):
        if controller.state is None:
            controller.state = {"messages": []}

        for command in request.commands:
            if command.type == "add-message":
                controller.state["messages"].append(command.message)

        async for message in your_agent.stream():
            controller.state["messages"].append(message)

    stream = create_run(run_callback, state=request.state)
    return DataStreamResponse(stream)
```

Mutating `controller.state` *is* the protocol: `state["message"] = "Hello"` emits `set`,
`state["message"] += " World"` emits `append-text`. Cancellation is cooperative —
`controller.is_cancelled` and `controller.cancelled_event` are set when the client disconnects,
with a ~50 ms shutdown window before forced cancellation.

**React side:**

```tsx
const converter = (state: State, meta: AssistantTransportConnectionMetadata) => ({
  messages: state.messages,
  isRunning: meta.isSending || false,
});

const runtime = useAssistantTransportRuntime({
  initialState: { messages: [] },
  api: "http://localhost:8010/assistant",
  converter,
});
```

Two facts that matter for a desktop host:

- `assistant-stream`'s only runtime dependency is `starlette>=0.37.2`, and it is used solely by
  `AssistantStreamResponse(StreamingResponse)`. The encoder itself,
  `StreamEncoder.encode_stream(stream)`, is a plain async generator of strings — **separable from
  HTTP** if a different bridge is ever wanted.
- The doc carries a standing warning: *"The wire format will migrate to Server-Sent Events (SSE)
  in a future release."*

The lower-friction alternative to the protocol is `ExternalStoreRuntime` raw: hand it `messages`,
`isRunning` and `onNew` from any store and push updates however you like. The protocol buys you
command queueing, optimistic pending commands, cancellation semantics and the Python encoder.

---

## 2. What we get for free, and what we do not

### 2.1 Shipped

| Capability | Ships? | Where it comes from |
|---|---|---|
| Composer (auto-resizing textarea, send, keyboard, a11y) | yes | `ComposerPrimitive.*`, dep `react-textarea-autosize` |
| Stop button | yes | `ComposerPrimitive.Cancel` gated on `s.thread.isRunning`; external store enables it via `onCancel` |
| Auto-scroll viewport + scroll-to-bottom | yes | `ThreadPrimitive.Viewport`, `ThreadPrimitive.ScrollToBottom` |
| Streaming markdown | yes, separate package | `@assistant-ui/react-markdown` (`react-markdown` + `remark-gfm`); or `@assistant-ui/react-streamdown` which handles half-written markdown mid-stream |
| Code blocks with highlighting | **no, not by default** | opt in via `@assistant-ui/react-syntax-highlighter` (peer `react-syntax-highlighter`) or the `shiki-highlighter` registry item |
| Attachments | yes | `AttachmentAdapter { accept, add, send, remove? }`; `CompositeAttachmentAdapter`, `SimpleImageAttachmentAdapter`, `SimpleTextAttachmentAdapter`. Supported on ExternalStore **and** AssistantTransport |
| Tool-call / generative UI | yes | toolkit entries with `render`, per-part `status`, partial args streaming, stock `ToolFallback`, `tool-group` for consecutive calls |
| Thread list / multi-chat | yes, adapter-shaped | `ThreadListPrimitive` + `ExternalStoreThreadListAdapter` (your store owns metadata) or `RemoteThreadListRuntime` (your database). Cloud is one of three options, not the only one |
| Branching / edit / regenerate | yes | built into `LocalRuntime`; on `ExternalStoreRuntime` each is unlocked by a callback: `setMessages` → branch switching, `onEdit` → edit button, `onReload` → regenerate |
| Collapsible streaming reasoning | **yes, exactly our requirement** | `reasoning` registry component + `MessagePrimitive.GroupedParts` with `groupPartByType({ reasoning: ["group-reasoning"] })`. `ReasoningRoot streaming={part.status.type === "running"}` auto-opens during streaming with a bottom-pinned live preview, defers permanently to the first manual toggle, collapses when the run ends |
| Approval gate that blocks the run | **yes** | see 2.2 |
| Message metadata / timing | yes | `metadata.timing` on `ChatModelRunResult`, `message-timing` component |

### 2.2 The approval gate, in detail

The backend emits a tool-call part carrying `approval: { id }` and ends the run with
`status: { type: "requires-action", reason: "tool-calls" }`. **"A tool call with a pending approval
pauses the run"**; the runtime does not call the adapter again until every pending approval is
decided. The renderer answers with `respondToApproval({ approved, reason? })`.

`approval.approved` is three-state: `undefined` = ask the user (the only state where responding is
legal), `true` = allowed, `false` = denied (the runtime synthesizes an error result with
`isError: true` so the model sees the denial). Richer decisions are supported as options with kinds
`allow-once` / `allow-always` / `reject-once` / `reject-always`, each optionally carrying `grants`
(patterns shown before the user commits) and `confirm` (a second confirmation step). The stock
`ToolFallback` renders the whole bar including confirmation.

> "Persistence is entirely host-owned: assistant-ui never stores a decision and never auto-answers
> future approvals."

A separate mechanism, `unstable_humanToolNames`, pauses the run on named tools and lets the *user
supply the tool result* rather than merely allow it.

**The caveat.** The docs say approval gates "require a runtime that implements them" and name only
AI SDK v7 and `LocalRuntime`. `unstable_humanToolNames` is documented as *"Only available on
LocalRuntime; not supported in DataStream."* The published type surface, however, shows
`ExternalStoreAdapterBase` carrying **`onRespondToToolApproval`**, `onAddToolResult`,
`onResumeToolCall` and `setToolStatuses` — so the external-store path has the seam; it is simply
undocumented. Treat "approval gate under AssistantTransport" as **unverified, not unsupported**.

### 2.3 Not shipped

| Missing | Detail |
|---|---|
| **Message-list virtualisation** | *"assistant-ui does not ship a virtualized thread component."* Default is `content-visibility: auto` + `contain-intrinsic-size`. A guide shows the supported composition with `@tanstack/react-virtual`, `unstable_useThreadMessageIds` and `ThreadPrimitive.Unstable_MessageById` — both flagged *"experimental and may change in any release"* |
| **A plan/checklist widget wired to the run** | `elements-agent-plan` ("A checklist the agent works through") exists in the registry but is **purely presentational**: `{ steps: readonly string[], activeIndex: number }`, no runtime binding, no per-step status beyond done/active/pending. Same for `elements-todo-list`, `elements-approval-card`, `elements-job-progress`. You wire the data |
| Syntax highlighting | opt-in package, see above |
| Persistence | no store is provided; history and thread-list are adapter slots you implement — or Cloud |
| Adapter gaps on AssistantTransport | speech, dictation, feedback and **suggestions** are not exposed; the docs say "drop down to `ExternalStoreRuntime` if you need them" |
| Suggestions on ExternalStoreRuntime | the adapter support matrix marks it `(no)` for every runtime except LocalRuntime and DataStream |
| CJS / UMD build | none — see §3.4 |

### 2.4 Our five requirements, scored

| Requirement | Verdict |
|---|---|
| Streaming reasoning in a collapsible accordion | **shipped**, purpose-built, auto-open-while-streaming included |
| Approval gate that blocks the run until answered | **shipped** as a first-class message-part state; the ExternalStore/AssistantTransport wiring is undocumented |
| File attachments | **shipped** as a 3-method adapter, works on the runtimes we would use |
| Per-session history | **shipped as a slot**, not as an implementation — `ExternalStoreThreadListAdapter` or a history adapter; storage is ours |
| Plan/checklist with per-step status marks | **not shipped as a component**; either a tool-call renderer we write, or `elements-agent-plan` plus our own data binding |

---

## 3. Hard constraints

### 3.1 License

MIT, uniformly. `LICENSE` at the repo root; `"license": "MIT"` in `packages/react/package.json` and
in the npm metadata for `@assistant-ui/react`, `@assistant-ui/react-markdown`,
`@assistant-ui/react-syntax-highlighter`, `@assistant-ui/react-streamdown`, `assistant-cloud`.
The Python package declares the MIT classifier in `python/assistant-stream/pyproject.toml`.
Copyright holder on the site footer: AgentbaseAI Inc.

### 3.2 Cloud: optional, and separable

The pricing page lists the library as **"Forever free & open source / MIT License / Customizable UI
components / Bring your own backend / Community support"**, beside a separate hosted product
`assistant-cloud`: Free (200 MAU), Pro $50/mo (500 MAU, $0.10 per extra MAU), Enterprise (custom).
No account is required to use the library; Cloud is opted into by constructing
`new AssistantCloud({...})` and passing it as `{ cloud }`.

Two footnotes:

- `assistant-cloud@^0.1.40` is a **hard `dependencies` entry of `@assistant-ui/react`**, not a peer
  or optional dep. It is inert without configuration but is in the tree and in the bundle graph.
- Resuming a run after a reconnect (`resumeApi` / `resumeStateApi`) requires their **sync server**,
  which the doc marks *"currently part of the enterprise plan"*.

### 3.3 React / Node

- Peer: `react: ^18 || ^19`, `react-dom: ^18 || ^19`. React 17 and 16 are explicitly unsupported.
  React 18 needs a one-line `forwardRef` patch to the shadcn `Button`.
- `@assistant-ui/react` declares **no `engines` field** — no Node version constraint on consumers.
  The monorepo's own root `package.json` pins `pnpm@11.10.0` and `node >=24`, which binds
  contributors, not users.
- **Nothing in the library requires Node at runtime.** It is browser React.

### 3.4 Build step

Mandatory once, at build time; absent at runtime.

`packages/react/package.json` is `"type": "module"` with a single export map entry
`"." → "./dist/index.js"`. There is **no CJS build and no UMD/IIFE bundle** — you cannot drop a
`<script>` tag on it. A bundler (vite / esbuild / webpack) must run once to produce assets.

The output, however, is static and server-free. `examples/with-browser-extension` builds
`@assistant-ui/react` with **esbuild + tailwind + postcss** into a Chrome extension whose only
scripts are `build` and `dev` — no server, no framework, loaded from a local origin. That is the
existence proof that a prebuilt bundle can be shipped.

### 3.5 Bundle size

Bundlephobia for `@assistant-ui/react@0.15.14`: **582,780 B minified / 173,931 B gzipped**,
18 dependencies. npm tarball: 2,305,255 B unpacked across 1,203 files. `"sideEffects": false`, so
tree-shaking applies to whatever you do not import. Add on top: `react` + `react-dom`,
`@assistant-ui/react-markdown` (`react-markdown`, `remark-gfm`), a highlighter,
`lucide-react` icons, and the Tailwind stylesheet.

Runtime deps of the core package: `zustand`, `radix-ui` (+7 `@radix-ui/*`), `zod`,
`react-textarea-autosize`, `assistant-stream`, `assistant-cloud`, `safe-content-frame`,
`@assistant-ui/{core,store,tap}`.

### 3.6 Styling

Two-tier, and the distinction is the whole answer:

- **Primitives are unstyled.** Radix-based, `asChild` composition, no CSS opinion. Tailwind is not
  required to use them.
- **The styled kit is shadcn/ui + Tailwind, and not optionally so.** The registry's `thread.tsx`
  contains 60 `className` attributes of Tailwind utilities (`"aui-thread-root bg-background
  @container flex h-full flex-col"`, `"fade-in slide-in-from-bottom-1 animate-in fill-mode-both"`),
  imports `cn` from `@/lib/utils`, `Button` from `@/components/ui/button`, and icons from
  `lucide-react`. `markdown-text` pulls `remark-gfm`. Every component also carries a stable
  `aui-*` class and `data-slot` attribute, so restyling from plain CSS is possible but means
  deleting the shipped classes.
- Delivery is the **shadcn CLI** against `https://r.assistant-ui.com/{name}.json` (style-aware form
  `https://r.assistant-ui.com/styles/{style}/{name}.json`). The CLI is a convenience — the JSON is
  public and each file's source is in a `content` field, so the components can be copied by hand.
- The registry currently exposes **139 items**, of which **122 are "Elements"** — a catalogue of
  presentational pieces (`elements-agent-plan`, `elements-approval-card`, `elements-todo-list`,
  `elements-reasoning-panel`, `elements-tool-timeline`, `elements-checkpoint-history`,
  `elements-subagent-list`, `elements-reviewable-diff`, `elements-terminal-block`, …).

### 3.7 Aliveness and churn

| Metric | Value |
|---|---|
| Stars / forks / watchers | 11,696 / 1,132 / 80 |
| Open issues | 62 |
| Latest `@assistant-ui/react` | 0.15.14, 2026-08-12 |
| Releases in the last 90 days | 36 (≈ one every 2.5 days) |
| Total versions since 2024-05-07 | 443 |
| npm downloads, week to 2026-08-15 | 1,446,383 (last month: 6,280,618) |
| Repo created | 2023-11-22 |

Breaking-change history: still pre-1.0. Minor lines and their first release —
`0.11` 2025-09-08, `0.12` 2026-01-24, `0.14` 2026-05-07, `0.15` 2026-07-28 — i.e. a breaking minor
roughly **every 2–3 months**, each with a migration guide (`docs/migrations/v0-11`, `v0-12`,
`v0-14`, `v0-15`, plus `toolkit-tools`). The v0-15 cycle already deprecated `components.Reasoning`
and `components.ReasoningGroup` in favour of `MessagePrimitive.GroupedParts`.

### 3.8 The stability contract (the constraint that actually bites)

From `docs/migrations/deprecation-policy`:

- **Stable** (>3 month deprecation notice): *Primitives* — and nothing else.
- **Beta** (<1 month notice): Runtime API, Context API, **message types**, **styled UI components**,
  primitive hooks, **attachment APIs**, shadcn/ui styles, Tailwind plugins.
- **Experimental** (removable without notice): anything `unstable_`, `experimental_`, `internal`.

And `docs/runtimes/concepts/stability`: `unstable_` means the surface "may change in any release
**including patch releases**", with the advice to pin exact versions and wrap every call site.

APIs we would depend on that carry that prefix: `unstable_createMessageConverter` (the message
converter **AssistantTransport itself uses**), `unstable_humanToolNames`, `unstable_capabilities`,
`unstable_useThreadMessageIds`, `ThreadPrimitive.Unstable_MessageById`, `unstable_getMessage`,
`unstable_state` / `unstable_annotations` / `unstable_data`.

### 3.9 Version skew between the JS and Python halves

`assistant-stream` is published twice on different version lines: **npm 0.3.37** (2026-08-09) and
**PyPI 0.0.36** (2026-08-04). Nothing pins them to each other; the repo carries interop fixture
tests (`python/assistant-stream/tests/test_interop_fixture.py`) rather than a shared version.

---

## 4. Alternatives in the same slot

The decisive column is **"needs a JS/TS server at request time"**, because our backend is Python
in-process. Distinguish it from "needs Node to build" — almost everything needs that, once.

| Candidate | License | JS server at runtime? | First-party Python | Pick it over assistant-ui when |
|---|---|---|---|---|
| **CopilotKit** 1.68.1, ★36,793 | MIT (Copilot Cloud = SaaS gate) | **Yes** — a Node/Next "Copilot Runtime" endpoint; the direct-to-agent mode is documented as *"intended for development and prototyping only"* | `copilotkit` 0.1.95 | you want LangGraph co-agents and shared app state (`useCoAgent`) out of the box and already run Node |
| **Vercel AI SDK UI** (`ai` 7.0.66 / `@ai-sdk/react`), ★26,240 | Apache-2.0 | No — the stream protocol is documented for non-JS backends (`examples/next-fastapi`) | none (a FastAPI example only) | you want the thinnest dependency and to own every pixel; you get hooks, no chat UI |
| **Chainlit** 2.11.1, ★12,388 | Apache-2.0 | No — FastAPI + uvicorn serving a prebuilt frontend from the wheel | `chainlit` (it *is* the backend) | the whole app is Python and a localhost HTTP+WebSocket server is acceptable. No in-process render mode; `--headless` only suppresses the browser launch. Oldest release of the six (2026-04-22) |
| **Streamlit** 1.61.1, ★45,556 | Apache-2.0 | No — a Python server serving a prebuilt frontend | `streamlit` | you want chat in ~20 lines of Python. Its rerun-the-whole-script model fights a stateful, long-running copilot |
| **Plain React + zustand** 5.0.15, ★58,577 | MIT | No — bundler at build time only | N/A, you define the transport | you need a transport that is not HTTP/SSE (a `QWebChannel` straight into the host process); the only option with zero runtime assumptions — and the only one where every feature in §2.1 is ours to write |
| **Open WebUI** 0.11.0, ★148,993 | **"Open WebUI License", non-OSI** — BSD-3 plus a branding clause (removing the branding needs ≤50 users/30 days, written permission, or an enterprise licence) | No — FastAPI serving a prebuilt SvelteKit bundle (145 MB wheel) | `open-webui` | you want a finished standalone multi-user chat product, not an embedded panel |

Only **CopilotKit** is a structural mismatch for a Python in-process backend. Chainlit, Streamlit
and Open WebUI are not mismatches but are *whole applications with their own server*, not component
libraries — they replace the UI layer rather than fill it.

---

## 5. Verdict

1. **Yes, assistant-ui is a sane target.** MIT, no account, no Node at runtime, and the
   Python-owns-the-state case is a first-party supported path (`AssistantTransport` +
   `assistant-stream` on PyPI + two Python backends in the repo), not something we would be forcing.
2. **Take `AssistantTransport`, or `ExternalStoreRuntime` under it.** Our typed event stream maps
   onto a JSON state snapshot far more naturally than onto an OpenAI-shaped message stream.
3. **The genuine wins are the ones we would spend weeks on:** the reasoning accordion with
   streaming auto-open, the approval gate as a run-pausing message-part state with allow-once /
   always / deny semantics, branching + edit + regenerate, and the attachment adapter contract.
4. **The gaps are known and small:** no virtualisation (a documented `@tanstack/react-virtual`
   composition), no syntax highlighting by default, and the plan/checklist exists only as
   unwired pixels.
5. **The single biggest thing that would bite us is API churn under a weak stability contract.**
   A 0.x library shipping a breaking minor every 2–3 months, where only Primitives are "stable",
   the Runtime API and message types get a <1 month deprecation window, and the message converter
   `AssistantTransport` itself depends on is `unstable_` — against a FreeCAD addon whose upgrade
   cadence is measured in FreeCAD releases. Pin exact versions, wrap every `unstable_` call site,
   and budget for a migration each time a minor lands. The runner-up bite: an approval gate under
   `AssistantTransport` is only proven by the type surface (`onRespondToToolApproval`), not by any
   doc or example — verify it with a spike before designing around it.

---

## Sources

Library, docs and source (all `assistant-ui/assistant-ui@main`):

- https://github.com/assistant-ui/assistant-ui
- https://www.assistant-ui.com/docs/runtimes/concepts/architecture
- https://www.assistant-ui.com/docs/runtimes/custom/overview
- https://www.assistant-ui.com/docs/runtimes/custom/assistant-transport
- https://www.assistant-ui.com/docs/runtimes/custom/external-store
- https://www.assistant-ui.com/docs/runtimes/custom/local-runtime
- https://www.assistant-ui.com/docs/runtimes/concepts/adapters
- https://www.assistant-ui.com/docs/runtimes/concepts/threads
- https://www.assistant-ui.com/docs/runtimes/concepts/stability
- https://www.assistant-ui.com/docs/migrations/deprecation-policy
- https://www.assistant-ui.com/docs/migrations/react-compatibility
- https://www.assistant-ui.com/docs/tools/tool-ui
- https://www.assistant-ui.com/docs/ui/reasoning
- https://www.assistant-ui.com/docs/ui/markdown
- https://www.assistant-ui.com/docs/ui/thread
- https://www.assistant-ui.com/docs/guides/virtualization
- https://www.assistant-ui.com/docs/guides/attachments
- https://www.assistant-ui.com/docs/installation
- https://www.assistant-ui.com/pricing
- https://www.assistant-ui.com/elements
- https://github.com/assistant-ui/assistant-ui/tree/main/python/assistant-stream
- https://github.com/assistant-ui/assistant-ui/blob/main/python/assistant-transport-backend/main.py
- https://github.com/assistant-ui/assistant-ui/tree/main/examples/with-assistant-transport
- https://github.com/assistant-ui/assistant-ui/tree/main/examples/with-browser-extension
- https://github.com/assistant-ui/assistant-ui/tree/main/examples/with-virtualized-thread
- https://raw.githubusercontent.com/assistant-ui/assistant-ui/main/api-surface/assistant-ui__react.ts
- https://raw.githubusercontent.com/assistant-ui/assistant-ui/main/packages/react/package.json

Registries and metrics:

- https://registry.npmjs.org/@assistant-ui/react
- https://api.npmjs.org/downloads/point/last-week/@assistant-ui/react
- https://bundlephobia.com/api/size?package=@assistant-ui/react@0.15.14
- https://pypi.org/pypi/assistant-stream/json
- https://api.github.com/repos/assistant-ui/assistant-ui
- https://api.github.com/repos/assistant-ui/assistant-ui/releases
- https://r.assistant-ui.com/registry.json
- https://r.assistant-ui.com/thread.json
- https://r.assistant-ui.com/elements-agent-plan.json
- https://r.assistant-ui.com/elements-approval-card.json

Alternatives:

- https://github.com/CopilotKit/CopilotKit — https://pypi.org/pypi/copilotkit/json
- https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol — https://github.com/vercel/ai/tree/main/examples/next-fastapi
- https://github.com/Chainlit/chainlit — https://docs.chainlit.io/data-layers/overview
- https://github.com/streamlit/streamlit — https://docs.streamlit.io/deploy/streamlit-community-cloud
- https://github.com/pmndrs/zustand
- https://raw.githubusercontent.com/open-webui/open-webui/main/LICENSE
