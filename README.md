# Daedalus

![Daedalus](Mod/resources/brand/banner.svg)

An assistant that lives inside FreeCAD's own UI. You state an **Intent** in your own
words — "make a gear with 24 teeth" — and an agent builds it in your **Document** as a
series of **Steps**.

It is text-to-CAD, with one difference from the usual meaning of that phrase: the output
is a live parametric feature tree you can keep editing, not a dead mesh or an STL.

> This is a diploma project. The inspiration is MEDA (ASME IDETC-CIE 2025), which routes
> seven agents through a group chat. This runs one agent loop instead, with a kernel that
> rehearses every Step and an Assertion that must hold before the run may finish.

---

## How a run works

**Every message you send is classified first.** One model call names it a design request,
a question about the run in progress, or a control instruction — Stop, abort — and only the
first starts a run. The panel and the SDK take the same route.

**A run is one agent loop.** The model reads the Intent and its attachments, then chooses
its own route over a small set of tools: the read tools (snapshot, describe an object,
dependencies), the render, and `code`, which writes a Step. Each Step is linted, rehearsed
in a persistent headless FreeCAD kernel, and only then landed on your Document as one
undoable transaction. The first `finish` is answered with the Domain's Assertion, and every
`finish` is refused until that Assertion holds.

**A Domain is what one loop builds.** It is a system prompt, an opening message, an
Assertion measured on the Document, and a done-check:

| Domain | Builds | Assertion |
|---|---|---|
| Part | one solid, from text, a sketch or a drawing | the part's measured facts agree with the drawing's table, and its rendered views are compared with the sheet |
| Assembly | parts joined in an Assembly | the solver reports no fault and every part is placed |
| BIM | a flat — Wall, Slab, Door, Window, Space — exported to IFC4 | the flat's measured facts hold |

The panel shows the Domain inferred from the Intent and the Document, and you can override it.

**A Tool set is what the loop may call.** The Domains differ in their prompts and
Assertions; the Tool registry (`src/core/tools.py`) is the only route to the Document.

**A PDF is a full attachment.** Drawings arrive as PDFs. The sheet is rendered here at the
resolution its own callouts need and cut into one picture per view. What is printed on the
sheet is extracted from its text layer rather than read off a picture, and a leader's end is
computed from the file's line work. A model that cannot read an attachment says so before
the run starts.

**Your objects are not the agent's.** An object you made is an Owned Object. Deleting one
asks first, through a card in the panel.

---

## What is in the repo

| Path | What lives there |
|---|---|
| `src/core/orchestrator.py` | The one call every message passes through: design, run question, or control |
| `src/core/agent/` | The agent loop (`solo.py`), the Domains (`domain.py`, `assembly.py`, `bim.py`, `reader.py`), and Domain inference |
| `src/core/loop/` | The loop's prompt helpers and its result |
| `src/core/tools.py` | The Tool registry — the only sanctioned route to the Document |
| `src/core/freecad_apply.py` | Lint → rehearsal → Apply, as one undoable transaction, and the FreeCAD-backed Tools |
| `src/core/kernel/` | The persistent headless FreeCAD: the parent half, the worker that runs a Step's code, and the rasteriser |
| `src/core/lint.py` | The AST lint that runs before a Step is rehearsed |
| `src/core/llm/` | The provider seam — LiteLLM, the `claude` CLI, plus a scripted client for tests |
| `src/core/sheets/` | Reads the drawing and checks a traced sketch against it |
| `src/core/events.py` | The event stream the loop narrates into, and the per-Session log |
| `src/core/narrate.py` | What an event says to a person — the panel and `tools/run.py` share it |
| `src/core/sessions.py` | The Sessions: the durable index, the naming, the bound. No Qt |
| `src/service/` | The hosted service: runs, workers, preferences, blobs |
| `src/tools/` | `run.py` drives one run against a live provider; `lane.py` sweeps models over sheets; `score.py` scores a saved result |
| `src/sdk.py` | The one assembly a caller drives — the only crossing between `src/`'s Qt-free engine and `Mod/` |
| `Mod/ui/daedalus_panel.py` | The dock: composer, transcript, Owned Object card, model picker |
| `Mod/ui/multi_tab_manager.py` | The stack of chat areas, one per Session |
| `Mod/ui/history_popup.py` | The Session Picker |
| `Mod/bridge/` | Qt-touching mirrors of `core/` concerns the UI needs directly: settings, provider fetch, logging, the main-thread marshaller |
| `config/prompts/` | The system, lint and message prompts, grouped by reader; copy to tune, kept out of the source |
| `Mod/DaedalusPreferences.py` | Registers the Preferences pages, which live in `Mod/ui/` |
| `InitGui.py`, `Init.py` | Workbench registration, at the repo root where FreeCAD runs them from |
| `tests/` | The suite |

---

## Install

FreeCAD 1.0, Python 3.11.

```bash
git clone <this repo> ~/Library/Application\ Support/FreeCAD/Mod/Daedalus
```

On Linux the addon directory is `~/.local/share/FreeCAD/Mod/`. A symlink from the Mod
directory to a checkout elsewhere works too, and is how this repo is developed.

Start FreeCAD and pick **Daedalus** from the workbench list. On first launch the addon
checks for `litellm`, `numpy` and `Pillow` and offers to install what is missing into
FreeCAD's own `AdditionalPythonPackages`.

Then open **Edit → Preferences → Daedalus → Providers**, paste an API key, and pick a
default model.

### Providers

OpenAI, Google, Anthropic, OpenCode Go, and any OpenAI-compatible endpoint added by name with **Add Custom Provider**.
Access goes through LiteLLM — one client for every provider, carrying Anthropic
thinking blocks and `cache_control` through the stream. Keys are yours and stay in your
FreeCAD settings; there is no service in the middle.

`src/core/model_catalog.py` is the registry of which providers exist and where each one
lives. There is no bundled model list: the real one is fetched from the provider on a
worker thread as soon as a key is present, so the model picker shows what your key
actually entitles you to and nothing it does not.

---

## Sessions

A **Session** is one continuous stretch of work on one Document, and the scope over which
Owned Objects are remembered. Every launch opens a new empty Session; earlier ones stay
in the **Session Picker**, which drops down from the top of the dock.

A Session enters the picker at the moment it gets its name — on its first Intent. It is
named from that Intent: the text truncated immediately so nothing is ever "Untitled",
replaced by a short generated title when it arrives. A name you type by hand always wins,
before or after. Which model writes the name is a Preferences setting; unset means
whichever model the chat is on.

The transcript is the event log, replayed through the same switch that rendered it live —
so a restored Session cannot drift from a running one.

---

## Tests

```bash
.venv/bin/python -m pytest -q
```

**FreeCAD is never mocked.** The suite is layered instead:

- **Level 1** (436 tests, 8 seconds) — pure logic with no FreeCAD and no Qt at all: the
  loop over a scripted LLM client, the lint, the event stream, what an event says, the
  Sessions, the title call. This is where TDD happens.
- **Level 2** (209 tests, marked `@pytest.mark.freecad`, two and a half minutes) — run
  against a real FreeCAD: the Tools, the kernel worker, and the Qt panel built
  offscreen against FreeCAD's own PySide2 — its arithmetic and its state machine, not
  its appearance.
- **Level 3** — by hand. How a widget looks under a theme is not something a test can
  tell you.

Level 2 is skipped when FreeCAD is not importable. `conftest.py` finds it at
`/Applications/FreeCAD.app/Contents/Resources/lib` or `/usr/lib/freecad/lib`, or wherever
`FREECAD_LIB` points.

> **Known wrinkle.** `conftest.py` puts the checkout on `sys.path` *before* importing
> FreeCAD, and FreeCAD then puts the Mod directory in front of it. If your Mod entry is a
> symlink to a different checkout, Level 2 tests exercise that one instead of the one you
> are in. It matters when running the suite from a git worktree, and it is silent — the
> tests pass or fail against code you are not looking at.
>
> To run a worktree against itself, give FreeCAD a user directory of its own:
>
> ```bash
> mkdir -p /tmp/fcuser/Mod
> ln -sfn "$PWD" /tmp/fcuser/Mod/Daedalus
> ln -sfn ~/Library/Application\ Support/FreeCAD/v1-1/AdditionalPythonPackages /tmp/fcuser/
> FREECAD_USER_HOME=/tmp/fcuser .venv/bin/python -m pytest -q
> ```
>
> The second symlink is not optional: `AdditionalPythonPackages` is where `litellm`,
> `httpx`, `numpy` and `Pillow` live, and without it collection fails on the imports.

---

## Scope

**In scope:** one Document, one part, parametric solid modelling — Part Design in the
usual range (sketch → pad / pocket / revolve / fillet), plus Assemblies and BIM flats, and whatever you
have selected live.

**Out of scope for now:** TechDraw, FEM, mesh and Surface. Each is a separate
API vocabulary and a separate failure taxonomy. Nothing enforces this technically — a
Step runs arbitrary FreeCAD Python — it is the boundary of what is demonstrated.

**What this is not:** not a script generator (Python is how a Step is applied, not a
saved artifact); not a chatbot about FreeCAD; not an autopilot; not a cloud
service; not an abstraction layer over other CAD.

**Who it is for:** engineers who already use FreeCAD and are tired of clicking what one
sentence describes. Not beginners.

---

## Vocabulary

The project is strict about its words: Intent, Step, Domain, Assertion, Tool set, Document, Snapshot,
Owned Object, Computed Reference, Tool, Look, Lint, Trap, Apply, Session, Session Name, Session Picker, Refusal. Tickets, tests and commit
messages use these and not synonyms.

The full Glossary and the Architecture Decisions live in the YouTrack knowledge base,
not in this repo. Issues are tracked in YouTrack, not GitHub Issues.
