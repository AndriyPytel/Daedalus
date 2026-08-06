# FreeCAD Copilot

An assistant that lives inside FreeCAD's own UI. You state an **Intent** in your own
words — "make a gear with 24 teeth" — and the Copilot turns it into a **Plan** of
**Steps**, shows you the Plan before anything is written, and then applies the approved
Steps to your **Document**.

It is text-to-CAD, with one difference from the usual meaning of that phrase: the output
is a live parametric feature tree you can keep editing, not a dead mesh or an STL.

> This is a diploma project. The inspiration and the deliberate departure is MEDA
> (ASME IDETC-CIE 2025), which routes seven agents through a group chat. This runs a
> fixed pipeline instead — the claim being that a fixed pipeline with Preflight and live
> introspection gives a controllable, measurable result where LLM-chosen routing gives
> neither exit conditions nor honest progress.

---

## How a run works

```
Intent ──► Plan ──► [ Plan Gate ] ──► Step 1 ──► Step 2 ──► … ──► Review
                          │             │                            │
                     you approve    Lint → Preflight → Apply     verdict returns
                     the whole Plan   (disposable worker)           to Plan
```

**Plan → Execute → Review is a fixed order with deterministic routing** (ADR-0003).
Plan produces the Steps, Execute works through them one at a time, Review judges the
outcome against the Intent, and Review's verdict returns to Plan — never to Execute.
There is no model-chosen speaker selection anywhere.

**Execute has one edge back to Plan** (ADR-0013). A Step that is out of date, or that
cannot be built once its attempts are spent, returns to Plan to be written differently
rather than ending the run. The Plan is a precise instruction while it is buildable and
advisory the moment it is not; Execute's only two moves are "do exactly this" and "hand
back", and the loop decides when that edge fires, not the model.

### The Plan Gate

One mandatory approval, covering every Step in the Plan. There is no per-Step
confirmation. Nothing that is not an **Owned Object** — something the assistant created
in this Session — is deleted without asking.

If you attached a drawing, the Gate also shows **how it was read** — the base dimensions,
each feature with its size and position, and, separately, what the drawing does not
define. That reading is the point of the Gate when there is a picture involved: a
misreading is something to correct here, in one sentence, rather than to recognise later
in the geometry. It is carried into Execute as text, and the drawing itself is shown to
Review, so the verdict compares the render to what you actually showed.

### Lint, then Preflight, then Apply

Before a Step's code runs anywhere:

- **Lint** reads the code as an AST. It carries only the rules a Preflight traceback
  cannot teach the way out of — a literal `Edge12` / `Face3` (ADR-0005), anything
  reaching for the GUI or the live selection, anything trying to make an object active.
- **Preflight** runs the Step against a throwaway copy of the Document in a separate,
  disposable headless FreeCAD process (ADR-0002). A Step that fails Preflight never
  reaches your Document. The worker dying is a normal outcome, not an incident: it is
  restarted and the crash comes back as a traceback the assistant can correct against.
- **Apply** commits what survived, as one undoable transaction.

A failed Step answers itself (ADR-0008): the traceback goes back to Execute with
introspection of whatever object the call failed on, so the next attempt has the real
attribute list rather than a second guess at the same wrong spelling.

### Where API knowledge comes from

Introspection of the running FreeCAD instance, not retrieval over documentation
(ADR-0006). Execute may spend a bounded number of read-only **Looks** at the Document
before it writes a Step's code (ADR-0009). What cannot be introspected is carried as a
**Trap** — one sentence of knowledge about this specific FreeCAD build, in the system
prompt, so a phase never discovers it by failing.

---

## What is in the repo

| Path | What lives there |
|---|---|
| `core/loop.py` | The Plan → Execute → Review loop, budgets, the Plan Gate |
| `core/tools.py` | The Tool registry — the only sanctioned route to the Document |
| `core/freecad_tools.py` | The Tools themselves: snapshot, describe_object, dependencies, apply_step, and the `TRAPS` |
| `core/lint.py` | The AST lint that runs before Preflight |
| `core/preflight.py` | The parent half: owns one warm headless FreeCAD, enforces the timeout |
| `core/preflight_worker.py` | The child half: runs inside that FreeCAD, renders the result |
| `core/apply.py` | Lint → Preflight → Apply, as one undoable transaction |
| `core/llm.py` | The provider seam — LiteLLM, plus a scripted client for tests |
| `core/events.py` | The event stream every phase narrates into, and the per-Session log |
| `core/session_title.py` | The short call that names a Session from its first Intent |
| `ui/copilot_panel.py` | The dock: composer, transcript, Plan Gate card, model picker |
| `ui/multi_tab_manager.py` | Sessions and the stack of chat areas |
| `ui/history_popup.py` | The Session Picker |
| `config/prompts/*.md` | The three phase prompts — copy to tune, kept out of the source |
| `Mod/` | Workbench registration and the Preferences pages |
| `tests/` | The suite; `tools/run_slice.py` drives a slice against a live provider |

---

## Install

FreeCAD 1.0, Python 3.11.

```bash
git clone <this repo> ~/Library/Application\ Support/FreeCAD/Mod/FreeCadCopilot
```

On Linux the addon directory is `~/.local/share/FreeCAD/Mod/`. A symlink from the Mod
directory to a checkout elsewhere works too, and is how this repo is developed.

Start FreeCAD and pick **Copilot** from the workbench list. On first launch the addon
checks for `litellm`, `numpy` and `Pillow` and offers to install what is missing into
FreeCAD's own `AdditionalPythonPackages`.

Then open **Edit → Preferences → Copilot → Providers**, paste an API key, and pick a
default model.

### Providers

OpenAI, Google, Anthropic, and a Custom/Local OpenAI-compatible endpoint. Access goes
through LiteLLM (ADR-0004) — one client for every provider, carrying Anthropic thinking
blocks and `cache_control` through the stream. Keys are yours and stay in your FreeCAD
settings; there is no service in the middle.

`config/models.json` is the offline fallback and the registry of which providers exist.
The real list is fetched from the provider on a worker thread as soon as a key is
present, so the model picker shows what your key actually entitles you to.

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

**FreeCAD is never mocked** (ADR-0007). The suite is layered instead:

- **Level 1** (291 tests) — pure logic with no FreeCAD at all: the loop over a scripted
  LLM client, the lint, the event stream, the title call. This is where TDD happens.
- **Level 2** (123 tests, marked `@pytest.mark.freecad`) — run against a real FreeCAD:
  the Tools, Preflight in a real worker, and the Qt panel built offscreen against
  FreeCAD's own PySide2 — its arithmetic and its state machine, not its appearance.
- **Level 3** — by hand. How a widget looks under a theme is not something a test can
  tell you, which is the finding behind ADR-0011.

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
> ln -sfn "$PWD" /tmp/fcuser/Mod/FreeCadCopilot
> ln -sfn ~/Library/Application\ Support/FreeCAD/v1-1/AdditionalPythonPackages /tmp/fcuser/
> FREECAD_USER_HOME=/tmp/fcuser .venv/bin/python -m pytest -q
> ```
>
> The second symlink is not optional: `AdditionalPythonPackages` is where `litellm`,
> `httpx`, `numpy` and `Pillow` live, and without it collection fails on the imports.

---

## Scope

**In scope:** one Document, one part, parametric solid modelling — Part Design in the
usual range (sketch → pad / pocket / revolve / fillet), plus whatever you have selected
live.

**Out of scope for now:** assemblies, TechDraw, FEM, mesh and Surface. Each is a separate
API vocabulary and a separate failure taxonomy. Nothing enforces this technically — a
Step runs arbitrary FreeCAD Python — it is the boundary of what is demonstrated.

**What this is not:** not a script generator (Python is how a Step is applied, not a
saved artifact — ADR-0001); not a chatbot about FreeCAD; not an autopilot; not a cloud
service; not an abstraction layer over other CAD.

**Who it is for:** engineers who already use FreeCAD and are tired of clicking what one
sentence describes. Not beginners — for a beginner the Plan Gate is a meaningless yes.

---

## Vocabulary

The project is strict about its words: Intent, Plan, Step, Plan Gate, Document, Snapshot,
Owned Object, Computed Reference, Tool, Look, Lint, Trap, Preflight, Preflight Worker,
Apply, Review, Session, Session Name, Session Picker, Refusal. Tickets, tests and commit
messages use these and not synonyms.

The full Glossary and the Architecture Decisions live in the YouTrack knowledge base
(`FCC-A-1` and `FCC-A-5`), not in this repo. Issues are tracked in YouTrack project
`FCC`, not GitHub Issues — see `youtrack.md`.
