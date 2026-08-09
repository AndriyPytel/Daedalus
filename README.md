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
Intent ──► Reading ──► Plan ──► [ Plan Gate ] ──► Step 1 ──► … ──► Assertion ──► Review
                                      │             │                              │
                                 you approve   Lint → Preflight → Apply       verdict returns
                                 the whole Plan   (disposable worker)             to Plan
```

**Reading → Plan → Execute → Assertion → Review is a fixed order with deterministic
routing** (ADR-0003, ADR-0018). Reading says what is on the drawing, Plan produces the
Steps, Execute works through them one at a time, Assertion measures the finished part
against the Acceptance list, Review judges the outcome against the Intent, and a failure
from either of the last two returns to Plan — never to Execute. There is no model-chosen
speaker selection anywhere.

**Reading is a context of its own, with nothing to build** (ADR-0018). It used to be part
of the same call that produced the Plan, and one context answering "what is on the
drawing" and "how do I build it" at once invents the numbers the second question needs:
one run read an overall length of 108 as 100 and then wrote itself the missing term that
made its own arithmetic close. A phase with nothing to build has no reason to write a
number in — where it cannot read a value it says so. It runs once per run, is reused by
every revision, and with nothing attached it does not run at all.

**Execute has one edge back to Plan** (ADR-0013). A Step that is out of date, or that
cannot be built once its attempts are spent, returns to Plan to be written differently
rather than ending the run. The Plan is a precise instruction while it is buildable and
advisory the moment it is not; Execute's only two moves are "do exactly this" and "hand
back", and the loop decides when that edge fires, not the model.

**Review judges the viewport** (ADR-0015). At the end of a run Review is given four views
of the result: isometric, front, top and right, captured once, with your camera put back.
One view is not enough: a mirrored gear tooth reads only from above. Headless, with no
viewport to capture, the Preflight Worker's rasteriser draws the Document once instead.
No Step produces a picture of its own — there is one render in a run, and it is the one
Review judges by.

**Review judges a Description too.** Four views cannot answer "is that hole Ø8", so beside
them Review and Assertion are given the Document as it now stands: every feature with its
parameters and its named sketch constraints, then an inventory of the geometry read off
the `Shape`, grouped so twenty identical holes are one entry with a count. Raw sketch
geometry is deliberately not in it.

**A PDF is a full attachment.** Drawings arrive as PDFs, and the "+" button offers them
beside PNG, JPEG, GIF and WebP. A PDF rides to the Reading phase and to Review as a `file`
content block; there is no page rendering on our side. A model that cannot read one says
so before the run starts, naming the file and the model, the same way an unreadable
attachment already does — and every attachment now goes out labelled as what it is rather
than as a PNG.

**An attachment leaves a trace.** The picture stays in the chat beside the Intent after
the chips clear, and the run records it on the event stream, so the Session's `.jsonl`
and a transcript reopened from the Session Picker both say what the run was shown. A
clipboard paste is a temp file the panel deletes when the run ends — the name is in the
record, so a Session opened tomorrow still names the drawing it no longer has.

### The Plan Gate

One mandatory approval, covering every Step in the Plan. There is no per-Step
confirmation. Nothing that is not an **Owned Object** — something the assistant created
in this Session — is deleted without asking.

If you attached a drawing, the Gate also shows **how it was read** — the base dimensions,
each feature with its size and position, and, separately, what the drawing does not
define — above the Plan that was written from it. That reading is the point of the Gate
when there is a picture involved: a misreading is something to correct here, in one
sentence, rather than to recognise later in the geometry. It is carried into Execute as
text, and the drawing itself is shown to Review, so the verdict compares the result to
what you actually showed.

**A reading is not prose.** Every value comes with what it is measured from — a named
face, axis, plane or edge — with a value that is either millimetres or a fraction of a
named overall, and with a `read` or `assumed` flag. A magnitude without its datum is the
whole defect: read "7 from the top face, 14 to the hole axis, over 30" as "two 7 mm
cheeks 16 mm apart" and a one-sided mounting has become a symmetric pair, with every
number still correct. A picture carrying no dimensions at all does not stop the run to
ask: exactly one number is assumed — an overall — and everything else is a fraction of
it, so a wrong guess is a wrong scale you fix with one number at the Gate rather than a
wrong shape. The Acceptance list inherits both the datum and the flag, because Assertion
writes its checks from that list and would otherwise measure the part against an invented
number and report a pass.

### Lint, then Preflight, then Apply

Before a Step's code runs anywhere:

- **Lint** reads the code as an AST. It carries only the rules a Preflight traceback
  cannot teach the way out of — a literal `Edge12` / `Face3` (ADR-0005), anything
  reaching for the GUI or the live selection, anything trying to make an object active.
- **Preflight** runs the Step against a throwaway copy of the Document in a separate,
  disposable headless FreeCAD process (ADR-0002), and measures the copy against the
  Step's Promise before Apply (ADR-0014). A Step that fails Preflight never
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
| `core/loop.py` | The Reading → Plan → Execute → Assertion → Review loop, budgets, the Plan Gate |
| `core/tools.py` | The Tool registry — the only sanctioned route to the Document |
| `core/freecad_tools.py` | The Tools themselves: snapshot, describe_object, dependencies, apply_step, and the `TRAPS` |
| `core/lint.py` | The AST lint that runs before Preflight |
| `core/preflight.py` | The parent half: owns one warm headless FreeCAD, enforces the timeout |
| `core/preflight_worker.py` | The child half: runs inside that FreeCAD, measures the Promise |
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
