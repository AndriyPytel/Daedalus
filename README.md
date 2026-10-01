# Daedalus

An assistant that lives inside FreeCAD's own UI. You state an **Intent** in your own
words — "make a gear with 24 teeth" — and the Daedalus turns it into a **Plan** of
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
                                 the whole Plan  (in the kernel)                  to Plan
```

**Every message you send is classified before it reaches a phase.** One model call names
it a design request, a question about the run in progress, or a control instruction —
Stop, abort — and only the first of those starts the loop below. The panel and the SDK
take the same route.

**Design → Plan → Execute → Assertion → Review is a fixed order with deterministic
routing** (ADR-0003, ADR-0018, ADR-0019). Design says what the part is, Plan produces the
Steps, Execute holds one kernel open for the phase and declares each Step's code the
moment it lands there (ADR-0027), Assertion measures the finished part
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

**The sheet is rendered here, and it is not sent whole** (DAE-102). It used to ride along
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

**The extracted list carries notation, not bare numerals.** Measured on the six
modelmania sheets: `Ø` is drawn as geometry and is not in the text layer, so it is matched
against a numeral's own bounding box and put back — 23 of 23 diameters, no false positives,
`(R30)` left alone. `R`, `°` and `±` are typed characters and survive as printed. What
still has to be read off the views is what the far end of a leader *is*, not what a numeral means.

**A leader is followed by arithmetic.** In a vector PDF the hairline is a list of
endpoints, so where a callout's leader ends is computed rather than judged on a 14 px
picture of it. On `mm25-p1-1.pdf` the `(R30)` leader starts on the left bore's axis and
its arrowhead lands on the wall of the central pocket — two features at two ends of one
line, which is exactly the case the description prompt warns about and exactly the one
that is hopeless to see. What the far end *is* remains the reading.

**The line work goes out too, in millimetres of the part** — every line and arc of each
view, chained out of the file and fitted, x to the right of the view's left edge and y up
from its bottom. A drawing says most of what it says in lines, and none of that was ever
handed over. Measured on the 2023 pipe clamp: the run asked the user how high the arm
meets the ring, was told the views do not answer, and assumed it centred on the ring axis
— while the elevation carries the ring's centre line at y=48.01 mm and the arm's own two
faces at 37.99 and 23.00, an arm 10.0 mm *below* the axis and flush with the ring's lowest
tangent. The part was built 17.5 mm out on a distance the file gives to a hundredth of a
millimetre. A centre line is an axis, a dimension's extension lines are drawn from the
faces that dimension measures, and a contour is the part; where a view's body was placed
as a raster the outline is not in the line work and that view says so.

**A value the geometry settles is `derived`, not a guess and not a gap.** The description
had two flags — `read` for what is printed and `assumed` for what a person supplied — so
anything the views fix without a callout, a tangency, a silhouette drawn to scale, had
nowhere legal to live and fell into `not_defined`, out to the user, and back as an
invention. `derived` carries `from`, one line naming what fixes it, and the coverage check
counts it exactly as it counts a printed number.

**Every page is read, and the pages are not one sheet.** The reader took page 1 and said
so in a note. Page 2 of the 2023 file prints `6.6 X 8.0 THRU ALL` — the width of the slot
the recorded run asked the user about and then invented as 8 mm. But page 2 of these
drawings is a *revision* of the same part, so the sheet now says so in the file's own
words, and the description is the part as the last page leaves it: what the later page
changes comes from it, what it does not mention stays as the earlier page had it, and a
disagreement between them is a conflict for the Gate. A description holding both
revisions at once — a Phase 1 hole and the Phase 2 slot that replaces it — is a part no
Plan can build.

**A revision of a Plan is shown the Plan it is revising.** The coverage check told Plan
which entries nothing built and then asked for a new Plan without showing it the one it
had written, so Plan answered the complaint alone: two Steps for the two entries named,
dropping the nine that covered the rest. Told those nine were now unplanned, it wrote them
again and dropped the two. Four rewrites, the whole revision budget, and not one Step ever
ran. `the_plan_you_wrote` rides with every rewrite now, and the instruction is to amend it
rather than replace it.

**The outside being nobody's stops the run** (ADR-0019's oldest finding, finally acted
on). "No feature carries the outer contour" has been raised for as long as the check has
existed, and in the recorded run it stood in `conflicts` while the Gate showed eight
Steps, the user approved, and the Plan padded a profile it made up. It is now put to the
drawing, then to the user, and a run that still cannot say what shape the part is does not
plan one. Every other conflict rides to the Gate as before: this is the only one that says
the *outside* is missing, and no later phase can notice it.

**One cavity is one feature.** The same hole in the material is drawn in the plan,
sectioned, and blown up in a detail, and the recorded run wrote two features out of one —
the second with an empty `position`, because its place was already on the first. Plan built
both, inventing a width for one and an R10 circle for the other, and the circle cut the
Body into two solids and ended the run. A cavity nothing places is now a conflict, and it
names the other cavity that shares its view.

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
  After the recompute the copy is read for seven failures the model cannot see from its own
  code: an object left in error, a fillet whose Base mixes concavities, a feature that
  moved no material, a feature no Body claims, a Body left in more than one piece, a Body
  whose Shape fails OCC's `isValid()`, and a solid with a negative volume. Each comes back
  as the reason, not as "it worked".
- **Apply** commits what survived, as one undoable transaction. The transaction recomputes
  inside itself and compares what landed against the signature the kernel verified: a
  mismatch is **drift** — your Document disagreeing with the kernel's, not the Step being
  wrong — and it is rolled back rather than accepted. Deleting more than one modelling
  object is a **rebuild**, not a correction, and needs your say-so; a sketch, datum,
  Measurement or spreadsheet does not count toward that, since a Pad and its sketch go
  together.

A failed Step answers itself (ADR-0008): the traceback goes back to Execute with
introspection of whatever object the call failed on, so the next attempt has the real
attribute list rather than a second guess at the same wrong spelling.

### Where API knowledge comes from

Introspection of the running FreeCAD instance, not retrieval over documentation
(ADR-0006). Execute may spend a bounded number of read-only **Looks** at the Document
before it writes a Step's code (ADR-0009, ADR-0027) — `describe_type` among them, which
instantiates a type in a throwaway Document so its properties, and the spellings its
enumerations accept, are readable before the first object of it exists. What cannot be
introspected is carried as a
**Trap** — one sentence of knowledge about this specific FreeCAD build, in the system
prompt, so a phase never discovers it by failing. A Trap is not only that sentence: each
seed Trap carries the script that reproduces it against the running build, so a claim can
be re-checked instead of believed, and the ones a script disproved are kept as retired
Traps with the reason.

---

## What is in the repo

| Path | What lives there |
|---|---|
| `src/core/orchestrator.py` | The one call every message passes through: design, run question, or control |
| `src/core/loop/` | The Design → Plan → Execute → Assertion → Review loop, budgets, the Plan Gate |
| `src/core/design/` | The part Design holds: the merge that never overwrites, and how it reads at the Gate |
| `src/core/agent/` | The multi-turn phase agent, and the visibility contract each phase runs under |
| `src/core/tools.py` | The Tool registry — the only sanctioned route to the Document |
| `src/core/freecad_apply.py` | Lint → Preflight → Apply, as one undoable transaction, and the FreeCAD-backed Tools themselves: snapshot, describe_object, dependencies, apply_step, and the `TRAPS` |
| `src/core/kernel/` | The persistent headless FreeCAD: the parent half, the worker that runs a Step's code, and the Preflight rasteriser |
| `src/core/lint.py` | The AST lint that runs before Preflight |
| `src/core/llm/` | The provider seam — LiteLLM, the `claude` CLI, plus a scripted client for tests |
| `src/core/sheets/` | Reads the drawing and checks a traced sketch against it |
| `src/core/events.py` | The event stream every phase narrates into, and the per-Session log |
| `src/core/narrate.py` | What an event says to a person — the panel and `tools/run.py` share it |
| `src/core/sessions.py` | The Sessions: the durable index, the naming, the bound. No Qt |
| `src/core/session_title.py` | The short call that names a Session from its first Intent |
| `src/sdk.py` | The one assembly a caller drives — the only crossing between `src/`'s Qt-free engine and `Mod/` |
| `Mod/ui/daedalus_panel.py` | The dock: composer, transcript, question card, model picker |
| `Mod/ui/multi_tab_manager.py` | The stack of chat areas, one per Session |
| `Mod/ui/history_popup.py` | The Session Picker |
| `Mod/bridge/` | Qt-touching mirrors of `core/` concerns the UI needs directly: settings, provider fetch, logging, the main-thread marshaller |
| `config/prompts/` | The phase, lint and message prompts, grouped by reader (`system/`, `lint/`, `messages/`); copy to tune, kept out of the source |
| `Mod/DaedalusPreferences.py` | Registers the three Preferences pages, which live in `Mod/ui/` |
| `InitGui.py`, `Init.py` | Workbench registration, at the repo root where FreeCAD runs them from |
| `tests/` | The suite; `src/tools/run.py` drives a slice against a live provider |

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

OpenAI, Google, Anthropic, OpenCode Go, and a Custom/Local OpenAI-compatible endpoint.
Access goes through LiteLLM (ADR-0004) — one client for every provider, carrying Anthropic
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
(`DAE-A-1` and `DAE-A-5`), not in this repo. Issues are tracked in YouTrack project
`DAE`, not GitHub Issues — see `youtrack.md`.
