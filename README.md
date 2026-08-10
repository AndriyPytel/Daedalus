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
Intent ──► Design ──► Plan ──► [ Plan Gate ] ──► Step 1 ──► … ──► Assertion ──► Review
                                      │             │                              │
                                 you approve   Lint → Preflight → Apply       verdict returns
                                 the whole Plan   (disposable worker)             to Plan
```

**Design → Plan → Execute → Assertion → Review is a fixed order with deterministic
routing** (ADR-0003, ADR-0018, ADR-0019). Design says what the part is, Plan produces the
Steps, Execute writes the code for all of them in one call and the loop applies them one
at a time (ADR-0021), Assertion measures the finished part
against the Acceptance list, Review judges the outcome against the Intent, and a failure
from either of the last two returns to Plan — never to Execute. There is no model-chosen
speaker selection anywhere.

**Design is a context of its own, with nothing to build** (ADR-0018, ADR-0019). It used
to be part of the same call that produced the Plan, and one context answering "what is on
the drawing" and "how do I build it" at once invents the numbers the second question
needs: one run read an overall length of 108 as 100 and then wrote itself the missing term
that made its own arithmetic close. A phase with nothing to build has no reason to write a
number in — where nobody supplied a value it says so. It runs once per run against the
sheet, is reused by every revision, and with nothing attached it does not run at all.

**The part is a structure that only ever grows** (ADR-0019). Design holds it as datums, an
envelope, features — each with an id, a kind, a topology, a position from a named datum
and the views it was seen in — plus what is in conflict and what nobody defined. Every
source merges into that one model and none overwrites it: a field already filled cannot
change, and two dimensions that cannot both be true stay recorded as a conflict for you to
settle at the Gate rather than being reconciled by the machine. One run read the depth as
100 mm off a section and planned 150 × 50 × 50 two turns later; the 100 was never
overruled, it was simply gone. Plan is handed that one model under one key, so there is
nothing left for it to adjudicate.

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
beside PNG, JPEG, GIF and WebP. A model that cannot read one says so before the run
starts, naming the file and the model, the same way an unreadable attachment already does
— and every attachment goes out labelled as what it is rather than as a PNG.

**The sheet is rendered here, and it is not sent whole** (FCC-102). It used to ride along
as an opaque `file` block, with the provider choosing the resolution — and it chose
against us. Measured on `mm25-p1-1.pdf`: an 11 × 8.5 in sheet arrives at the provider's
own 1568 px cap, which is 143 DPI, which puts **14 px of ink in a dimension digit**. 14 px
is not illegible, and that is what makes it expensive — it reads correctly most of the
time, so misreadings arrive one at a time, already flagged `read`, into a model designed
never to overwrite them. "108 read as 100" is that failure and not a worse one.

So Design is now shown the sheet for its layout and then **one picture per view**, cut out
of it and rendered at the DPI its own callouts ask for — 279 for this sheet, set by the
12.9 pt dimension text and deliberately not by the 4.1 pt confidentiality paragraph in the
title block, which is 28 of the sheet's 89 words and would put every crop at 492 DPI to
render something nobody models from. A crop is never wider than the cap it would be
downscaled to; a region too big to be both whole and legible is sent in overlapping
pieces.

**What is printed on the sheet is extracted, not read.** A vector PDF carries the
draughtsman's own strings with their coordinates, so `printed_on_the_sheet` is the file's
text layer rather than the model's account of what it noticed. That matters because the
old check could not fail in the one way that counts: a callout nobody saw is missing from
the sweep *and* from the part, and counting one against the other finds nothing wrong.
Held against the file, a dropped dimension comes back to Design naming the number, and
what comes back goes through the same merge as everything else — bounded by the same
re-read ceiling as a question put to the sheet.

**The extracted list is numerals, not notation.** Measured on the same sheet: `Ø` is drawn
as geometry and is not in the text layer at all, so a bore printed `Ø35 THRU` extracts as
`35 THRU`. `R` survives, `(R30)` survives whole. The numbers are exact and what they
*mean* is still read off the views — which is the boundary this whole phase is built on.

**A leader is followed by arithmetic.** In a vector PDF the hairline is a list of
endpoints, so where a callout's leader ends is computed rather than judged on a 14 px
picture of it. On `mm25-p1-1.pdf` the `(R30)` leader starts on the left bore's axis and
its arrowhead lands on the wall of the central pocket — two features at two ends of one
line, which is exactly the case the description prompt warns about and exactly the one
that is hopeless to see. What the far end *is* remains the reading.

**All of it degrades rather than fails.** PyMuPDF is a required package the addon can run
without: with it missing, or a file it cannot open, or a raster instead of a PDF, the
attachment goes out exactly as it did before. A raster gets the crops but no text layer,
off its own ink — and the paper is measured off the edge of the picture rather than
assumed to be white, because a drawing exported on a black background read as "dark means
ink" comes back as one crop of the whole sheet.

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
define — above the Plan that was written from it. That description is the point of the
Gate when there is a picture involved: a misreading is something to correct here, in one
sentence, rather than to recognise later in the geometry. It is carried into Execute as
text, and the drawing itself is shown to Review, so the verdict compares the result to
what you actually showed.

**A description is not prose.** Every value comes with what it is measured from — a named
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

**The question belongs to whoever owns the description.** Design asks you itself, before
any Plan exists, for what it sees empty in its own model. A question Plan raises does not
reach you at all: it goes back to Design, which either finds it on the sheet — the sheet is
asked before you are, since you attached it so you would not have to answer for it — or
has you answer. Either way the answer is merged into the description before the next Plan,
so what Plan reads is one account of the part and not an answer beside it. The transcript
shows which happened, and an answer read off the drawing is a `read` value where one you
gave is `assumed`.

**The description opens with a sweep of the sheet** — every callout printed on it, listed
with the view it is in — and every entry on that list has to end up either on the part or in
the not-defined list with a reason, so a dimension that was missed is something you can
see was missed. And **"not dimensioned" is not "not defined"**: which way an arc bows,
which side of a section is material, whether a cut goes in or a boss stands out, whether a
hole goes through — none of that is dimensioned on any drawing and all of it is on the
sheet. A radius in particular is only half read; without its centre placed, `R30`
describes a wall that curves in and a wall that curves out equally well.

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
before it writes the Plan's code (ADR-0009, ADR-0021) — `describe_type` among them, which
instantiates a type in a throwaway Document so its properties, and the spellings its
enumerations accept, are readable before the first object of it exists. What cannot be
introspected is carried as a
**Trap** — one sentence of knowledge about this specific FreeCAD build, in the system
prompt, so a phase never discovers it by failing.

---

## What is in the repo

| Path | What lives there |
|---|---|
| `core/loop.py` | The Design → Plan → Execute → Assertion → Review loop, budgets, the Plan Gate |
| `core/design.py` | The part Design holds: the merge that never overwrites, and how it reads at the Gate |
| `core/tools.py` | The Tool registry — the only sanctioned route to the Document |
| `core/freecad_tools.py` | The Tools themselves: snapshot, describe_object, dependencies, apply_step, and the `TRAPS` |
| `core/lint.py` | The AST lint that runs before Preflight |
| `core/preflight.py` | The parent half: owns one warm headless FreeCAD, enforces the timeout |
| `core/preflight_worker.py` | The child half: runs inside that FreeCAD, measures the Promise |
| `core/apply.py` | Lint → Preflight → Apply, as one undoable transaction |
| `core/llm.py` | The provider seam — LiteLLM, plus a scripted client for tests |
| `core/events.py` | The event stream every phase narrates into, and the per-Session log |
| `core/narrate.py` | What an event says to a person — the panel and `run_slice` share it |
| `core/sessions.py` | The Sessions: the durable index, the naming, the bound. No Qt |
| `core/session_title.py` | The short call that names a Session from its first Intent |
| `ui/copilot_panel.py` | The dock: composer, transcript, question card, model picker |
| `ui/multi_tab_manager.py` | The stack of chat areas, one per Session |
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

- **Level 1** (436 tests, 8 seconds) — pure logic with no FreeCAD and no Qt at all: the
  loop over a scripted LLM client, the lint, the event stream, what an event says, the
  Sessions, the title call. This is where TDD happens.
- **Level 2** (209 tests, marked `@pytest.mark.freecad`, two and a half minutes) — run
  against a real FreeCAD: the Tools, Preflight in a real worker, and the Qt panel built
  offscreen against FreeCAD's own PySide2 — its arithmetic and its state machine, not
  its appearance.
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
