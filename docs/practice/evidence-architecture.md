# Architecture and method evidence

Source material: `README.md`, ADR-0001 through ADR-0025 (YouTrack knowledge base,
article `FCC-A-5`, project `FCC`), and the `core/`, `ui/`, `tools/`, `Mod/`, `utils/`
source trees as they stand in this checkout.

## 1. The pipeline

```
                         ┌──────────────────────── Plan Gate ────────────────────────┐
                         │        you approve the whole Plan, or REJECT it           │
                         └────────────────────────────┬───────────────────┬──────────┘
                                                        │                   │
Intent ──► Design ──► Plan ──────────────────────────► │            (revision) ◄────┐
             ▲  ▲                                      ▼                            │
             │  └──────────────── unresolved conflict ─┤                            │
             │                                    Step 1 ─► Step 2 ─► … ─► Step N    │
             │                                    (Lint → Preflight → Apply, each)   │
             │                                             │      ▲                  │
             │                                    out-of-date / exhausted Step ──────┘
             │                                             ▼
             │                                        Assertion
             │                                     (asserts the Acceptance
             │                                      list, derived from Design)
             │                                             │
             │                                    failed check ──────────────────────┘ (to Plan)
             │                                             ▼
             │                                          Review
             │                                (four viewport views + Description,
             │                                 judged against the Intent)
             └────────────────── rejected verdict ────────┘ (to Plan)
```

**Design.** Consumes the Intent, any attachments (PDF/PNG/JPEG/GIF/WebP drawings), and
— on a revision — the questions the drawing itself could not answer. It produces one
growing, declarative structure: datums, an envelope, features (each with an id, a kind,
a topology, a position from a named datum, and the views it was seen in), plus
`conflicts` and `not_defined`. Fields merge and never overwrite (ADR-0019); as of
ADR-0024 the phase first gathers all the evidence it can reach and then distils that
evidence into the one document every later phase reads, discarding provenance
(`from`, `seen_in`) and title-block noise. As of ADR-0023, Design runs even with no
attachment — it describes the part from the Intent's own words — and the Intent rides
into every one of its turns rather than only the first. It runs once per run against the
sheet and is reused by every revision; a value nobody supplied is settled by the model
itself under a required `because` (ADR-0022) rather than blocking the run, except for
the one case a drawing genuinely cannot answer.

**Plan.** Consumes the distilled description under one key and produces the Steps: the
whole modelling plan for the part, in one call, each Step citing (`satisfies`) the
description entries it builds (ADR-0020, ADR-0021). The Acceptance list is no longer
authored by Plan — it is derived directly from the description's own `read`/`assumed`
entries (ADR-0020, superseding part of ADR-0017) — so Plan owns only the citation, not
the numbers. Before the Plan Gate, the loop checks that every `read` entry is covered by
at least one Step's `satisfies`; an uncovered Plan is handed back as a revision before a
user ever sees it.

**Plan Gate.** Consumes the Steps, the Acceptance list, the description (including a
"decided for you" section for anything the model settled on its own), and any open
conflict. Produces exactly one decision: approve the whole Plan, or REJECT it — there is
no per-Step confirmation. Nothing that is not an Owned Object is deleted without asking.
Routes to Execute on approval, back to Plan on rejection.

**Execute.** Consumes one Step at a time (the whole Plan is written in one call —
ADR-0021) plus a bounded number of read-only Looks at the live Document (introspection,
not documentation retrieval — ADR-0006, ADR-0009). Produces FreeCAD Python for that Step,
run through Lint → Preflight → Apply (ADR-0002, ADR-0005, ADR-0012, ADR-0014). A failed
Preflight returns the traceback, with introspection of the object the call failed on, to
Execute for another attempt (ADR-0008); a Step that is out of date or exhausts its
attempts is the one edge back to Plan (ADR-0013). Everything that survives Preflight is
committed by Apply as one undoable transaction, and only if it demonstrably changed the
Document (ADR-0010).

**Assertion.** Consumes the finished Document and the Acceptance list, and writes and
runs asserts against it — read-only, once per run, in the Preflight Worker against a
disk copy so the live Document is never reachable from it (ADR-0017, refined by
ADR-0020). One assert per entry, the entry itself as the failure message; an entry that
cannot be measured is marked `uncovered` rather than approximated, and passes to Review.
A failed assert equals a rejected Review outcome and routes to Plan as a revision, with
an explicit up-front decision on whether the user wants it fixed, taken as-is, or
restarted from nothing (ADR-0017).

**Review.** Consumes the Intent, four fixed viewport renders (isometric, front, top,
right — captured once, camera restored, ADR-0015), the Description, and whatever
Assertion left `uncovered`; it is never shown the Plan or the code that built the part
(ADR-0003, ADR-0008). It produces a verdict: accepted, or rejected with feedback that
routes back to Plan — never to Execute.

## 2. Key architectural decisions

1. **The Document is the source of truth, not a generated script (ADR-0001).** Problem:
   a Copilot must let the user hand-edit the model between Steps and have those edits be
   real; a script-as-truth design either ignores hand edits or maintains a second
   history that drifts from FreeCAD's own feature tree. Consequence: execution runs
   inside FreeCAD's own process, each Step is one undo-able transaction.
2. **Every Step is preflighted in a disposable worker before it touches the Document
   (ADR-0002).** Problem: generated code runs inside FreeCAD's own process, where a bad
   call into the C++ core is a segfault, not a Python exception, and can take unsaved
   work with it. Motivating evidence: the worker dying is treated as an expected,
   restartable outcome rather than an incident.
3. **Plan → Execute → Review is a fixed pipeline, not a multi-agent group chat
   (ADR-0003).** Problem: MEDA's inspiration routes seven agents through an AutoGen
   `GroupChat` with `speaker_selection_method="auto"`; LLM-chosen routing gives no exit
   conditions and no honest progress. The loop decides when the one back-edge (Execute →
   Plan, ADR-0013) fires, never the model.
4. **LiteLLM for provider access, not LangChain and not a single OpenAI-compatible
   client (ADR-0004).** Problem: dependency weight in FreeCAD's shared embedded
   interpreter is a real cost, and the OpenAI-compatibility layer drops Anthropic prompt
   caching, structured outputs and reasoning content. Confirmed by a spike
   (`tools/spike_litellm.py`, LiteLLM 1.93.0): thinking blocks and `cache_control` both
   survive the stream intact.
5. **A generated feature may never contain a literal `Edge12`/`Face3` (ADR-0005).**
   Problem: FreeCAD's topological names are unstable across recomputes; a literal
   reference is a landmine that Preflight cannot always catch before it detonates later.
6. **API knowledge comes from introspecting the running FreeCAD instance, not from
   retrieval over documentation (ADR-0006).** Consequence: Execute may spend a bounded
   number of read-only Looks (`describe_type` among them) before writing code, and what
   cannot be introspected is carried as a one-sentence Trap in the system prompt
   (ADR-0009).
7. **FreeCAD is never mocked in tests (ADR-0007).** Consequence: the test suite is
   layered — pure-logic Level 1 (no FreeCAD, no Qt), Level 2 against a real headless
   FreeCAD, Level 3 by hand.
8. **A failed Step answers itself; only unanswerable failures become lint rules
   (ADR-0008).** Problem: a second guess at the same wrong spelling wastes an attempt.
   The traceback plus introspection of the failing object is fed back to Execute, and
   only failure classes a traceback cannot teach a way out of graduate into Lint (a bare
   `except`, `.Shape` assigned on a `PartDesign::` feature — ADR-0012).
9. **A Step that changes nothing has not been done (ADR-0010).** Motivating failure: a
   twelve-Step plan's Step 2 defined a function and never called it; Preflight ran clean,
   the pipeline reported success, and the next Step had to build what Step 2 was already
   credited with. `_Touched` (the document observer) is now read as the verdict.
10. **Plan is the author; Execute either executes exactly or hands back (ADR-0013,
    amending ADR-0003 and ADR-0010's granularity reasoning).** A Step out of date or out
    of attempts returns to Plan to be rewritten, rather than ending the run.
11. **The result is a real PartDesign feature tree, and Lint enforces it (ADR-0012).**
    Motivating evidence: a 2026-08-06 QA round produced an ISO 4032 nut and a Model Mania
    offset link whose "features" were `PartDesign::Feature` shells wrapping raw `Part`
    booleans with invented `addProperty` fields — nothing rebuilds when a dimension
    changes, and no phase objected because Preflight recomputed clean.
12. **A Step carries a Promise and Preflight measures it, later refined to measuring the
    finished part instead of each Step (ADR-0014, then ADR-0017/ADR-0020).**
13. **Review judges the viewport, from a fixed set of views (ADR-0015).** Motivating
    finding: one view is not enough — a mirrored gear tooth reads correctly only from
    above.
14. **The panel has no colours of its own (ADR-0011).** Motivating finding: how a widget
    looks under a theme is not something a test can verify, so it is Level 3, by hand.
15. **Reading is a phase of its own, and the pipeline is five (later four/five again)
    phases (ADR-0018).** Motivating failure: a context that answers both "what is on the
    drawing" and "how do I build it" invents numbers the second question needs — one run
    read an overall length of 108 as 100 and then wrote itself the missing term that made
    its own arithmetic close.
16. **The part is one declarative structure that only ever grows (ADR-0019).**
    Motivating failure: one run read a depth as 100 mm off a section and planned
    150 × 50 × 50 two turns later — the 100 was never overruled, it was simply gone,
    because nothing enforced that a filled field cannot change.
17. **Plan writes an Acceptance list and a separate Assertion phase measures the result
    (ADR-0017), later: the Acceptance is derived from the description itself, not
    authored by Plan (ADR-0020).** Motivating failure: between the sheet and the assert
    there were two rewrites, and in one recorded run both lied — a 100 mm depth was
    checked as 50 mm and would have passed.
18. **One entry of the description is one Step, and every Step cites what it satisfies
    (ADR-0020).** Motivating failure: one physical cavity was planned three times from
    three different views (front, SECTION A-A, SECTION B-B) because nothing in the loop
    ever compared the description against the Plan.
19. **Execute writes the whole Plan in one call and may introspect a type before an
    instance of it exists (ADR-0021).**
20. **A value nobody supplied is settled by the model, recorded as `assumed` with a
    required `because`, and shown at the Gate; no site stops a run for want of an answer
    (ADR-0022).** Motivating failure: a user answered "You decide" twice mid-run and got
    blocked both times, once forced to hand-edit a YAML file to unblock a run that had
    already waited two ~90-second stretches on a `threading.Event`.
21. **The Intent is the scope of the run and rides in every Design turn (ADR-0023).**
    Motivating failure: a text-only Intent naming "Phase 1 of a two-page drawing" reached
    only one of eight Design call sites; the run described both drawing revisions,
    guessed, and built three left arms on one clamp.
22. **Design gathers evidence and then distils it; the distilled document is the artefact
    (ADR-0024).** Motivating measurement: one Design call carried ~54,000 tokens across
    13 turns and the description handed to Plan grew from 16,804 to 51,117 characters
    while the feature count never changed — reading more of the sheet made the run know
    less about the part; distillation cut the same document to 8,787 characters.
23. **A provider may be a subprocess on a subscription rather than a key through LiteLLM
    (ADR-0025, amending ADR-0004 in part).** `claude -p` on a Claude Max subscription is
    a third client implementation beside `LiteLLMClient` and `ScriptedLLMClient`, reached
    when no API key is configured; measured to preserve prompt caching, structured
    output and streamed reasoning content, contrary to an earlier draft's assumption.
24. **The sheet is rendered locally and never sent whole; what is printed is extracted,
    not read (README, not yet a numbered ADR at the time of writing).** Motivating
    measurement: an 11×8.5 in sheet sent as an opaque provider attachment arrives at
    143 DPI — 14 px of ink in a dimension digit — which is legible often enough that
    misreadings ("108 read as 100") arrive one at a time into a model designed never to
    overwrite them.

## 3. Technology stack

Read off the source, not assumed:

- **Language / runtime.** Python 3.11, running inside FreeCAD 1.0's own embedded
  interpreter (`Init.py` imports `FreeCAD`, `InitGui.py` imports `FreeCADGui`).
  Dependencies install into FreeCAD's own `AdditionalPythonPackages` directory
  (`core/dependency_manager.py`), not a project venv, because the addon shares that
  interpreter with every other FreeCAD addon.
- **Required third-party packages** (`core/dependency_manager.py::REQUIRED_PACKAGES`):
  `litellm` (LLM provider access), `numpy`, `Pillow` (`PIL`, image handling for
  drawings/renders), `pymupdf` (`fitz`, PDF rendering and text-layer extraction — used in
  `core/sheet_reader.py` and gated by `core/dependency_manager.py`), `PyYAML` (`yaml`,
  the part-file / Design-model serialisation in `core/part_file.py`).
- **LLM access layer.** `core/llm.py` wraps `litellm` as `LiteLLMClient`, plus a
  `ScriptedLLMClient` used only in tests, plus (ADR-0025) a `claude -p` subprocess client
  reached when no API key is configured. Configured providers
  (`core/model_catalog.py::_PROVIDER_BASE_URLS`): OpenAI, Anthropic, Google, OpenCode Go,
  Custom/Local (any OpenAI-compatible endpoint), plus the subscription route "Claude
  (subscription)". There is no bundled model list — `core/model_catalog.py` fetches
  `/models` from the provider on a worker thread once a key is present.
- **FreeCAD integration mechanism.** Direct in-process Python API calls (`FreeCAD`,
  `FreeCADGui` / `App`, `Gui`), not a REST bridge or an external script runner. A
  workbench (`Mod/CopilotWorkbench.py`) registers the addon inside FreeCAD's own
  workbench list. Preflight (`core/preflight.py`, `core/preflight_worker.py`) drives a
  second, disposable, headless `FreeCADCmd` process located the same way as the
  `claude` subprocess binary — env override, known paths, `shutil.which`.
- **UI stack.** Qt, through FreeCAD's own bundled bindings: `ui/copilot_panel.py` and
  siblings import `from PySide.QtCore` / `from PySide.QtGui` (FreeCAD 1.0's `PySide`
  compatibility shim); `Mod/pages/providers_page.py` imports `PySide2` directly. No web
  view, no separate frontend framework — the dock, the transcript, the Plan Gate, the
  Session Picker and the model picker are all native Qt widgets built and driven from
  Python.
- **Static analysis / safety.** `core/lint.py` parses generated code as a Python `ast`
  tree (no execution) before it ever reaches Preflight.
- **Prompts as data.** `config/prompts/*.md` — five phase prompts (`design.md`,
  `plan.md`, `execute.md`, `assertion.md`, `review.md`), kept as plain text outside the
  Python source so they can be tuned without a code change.
- **Testing.** `pytest` (`pytest.ini`), with a custom `freecad` marker for tests that
  need a real FreeCAD import; `tests/test_the_code_says_why_not_what.py` enforces the
  project's no-comments convention over `core`, `ui`, `utils`, `tools`, `Mod`.

## 4. Module map

Rough LOC via `wc -l` over `*.py` per top-level package, this checkout:

| Package | Responsibility | ~LOC |
|---|---|---|
| `core/` | The agent loop and everything it touches directly: `loop.py` (the Design→Plan→Execute→Assertion→Review state machine, budgets, the Plan Gate), `design.py` (the growing part model and its merge), `sheet_reader.py` (PDF/image drawing extraction — crops, text layer, line work), `llm.py` (the provider seam: LiteLLM, the scripted test client, the subscription client), `tools.py` / `freecad_tools.py` (the Tool registry, the only sanctioned route to the Document), `lint.py` (AST safety rules), `preflight.py` / `preflight_worker.py` (the disposable headless-FreeCAD check), `apply.py` (Lint → Preflight → Apply as one transaction), `events.py` / `narrate.py` (the event stream and its prose), `sessions.py` / `session_title.py` (the durable per-Document Session index), `part_file.py` (the YAML-serialised description), `arithmetic.py`, `sketch_check.py`, `model_catalog.py`, `dependency_manager.py`. | ~14,900 |
| `ui/` | The Qt panel: `copilot_panel.py` (dock, composer, transcript, question card, model picker), `multi_tab_manager.py` (one chat area per Session), `history_popup.py` (the Session Picker), `plan_widget.py` / `assertion_results_widget.py` / `review_verdict_widget.py` / `queue_widget.py` / `upload_widget.py` / `accordion_widget.py` (Plan Gate, Assertion and Review presentation), `design_tokens.py` / `typography.py` / `ui_helpers.py` (shared look, no colours of its own per ADR-0011). | ~5,600 |
| `Mod/` | Workbench and preferences: `CopilotWorkbench.py` (registers the workbench with FreeCAD), `CopilotPreferences.py`, `pages/providers_page.py` (the Providers preferences page — API keys, Test Connection). | ~560 |
| `utils/` | Cross-cutting support used by both `core` and `Mod`: `settings_manager.py` (FreeCAD preferences-backed settings, credentials), `provider_manager.py`, `logger.py`. | ~660 |
| `tools/` | Developer-facing scripts, not shipped to users: `run_slice.py` (drives a phase slice against a live provider), `spike_litellm.py` (the ADR-0004 spike). | ~360 |
| `config/prompts/` | The five phase prompts as Markdown, no code. | 1,184 lines of prompt text |
| `tests/` | The layered suite (Level 1 pure logic, Level 2 `@pytest.mark.freecad`, Level 3 manual) — 53 test files. | ~25,200 |

`core/` is by a wide margin the largest and most load-bearing package: `loop.py`
(3,352 lines) and `sheet_reader.py` (3,587 lines) alone account for roughly half of it,
which matches the architecture's own framing — the loop is the fixed pipeline itself,
and the sheet reader is the boundary between an opaque provider attachment and the
locally-rendered, locally-extracted evidence Design is built to read from ADR-0022
onward.
