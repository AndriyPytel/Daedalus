# Changelog

## 1.4.0 — 2026-11-14

### Breaking
- `run.py --task <folder>` opens a fixture (Part, edit, Assembly or BIM); `--intent` gives a bare Intent. It replaces five flags.
- `--base-step`, `--base-document`, `--base-ifc`, `--domain` and `--attach` are gone; `run.py` rejects them.
- The Gateway's Preferences are `{provider, model, effort, keys, addresses}`; no Role table is accepted.
- The `default_model` and `naming_model` settings are gone.

### Agent loop
- A `code` call lints once, inside Apply, with the GUI selection; the loop no longer lints.
- A `code` call that changes nothing is refused (`changed_nothing`), and Apply is the one place that rule lives.
- The Kernel is seeded once per run, by `start_run`; the panel and `run.py` no longer seed it.
- A Part finish measures once: one facts script serves the done-check and the Assertion.
- A Part finish refuses a Document with more than one solid, and a STEP that does not read back valid and manifold.
- The Part Domain owns its Step rules and hands them to the Kernel when it seeds; Assembly and BIM run none.

### Kernel and Apply
- The Kernel has five verbs: `rehearse`, `probe`, `seed`, `render`, `measure`. The wire protocol is private to it.
- `Kernel.measure` runs a facts script and reads the answer back; Domains no longer carry their own copy.
- `freecad_apply.py` is split into Apply, Document Tools (`core/document_tools.py`) and Traps (`core/freecad_traps.py`).
- `kernel_worker.py` is split into the wire server, the Step namespace (`step_namespace.py`) and the Step rules.
- Lint no longer checks the typeid of the Operation a Step names.

### Panel and providers
- One model and one Effort serve a whole run. The chat's model pill and Effort pill write them, and nothing else does.
- The router, Domain inference, the Session title and Assembly's part sub-agents use that same model and Effort.
- The Roles page in Preferences is gone.
- `Client.send` is the whole run: the lock, the seed, the Domain choice, the Document's name and the question callback. The panel and the Gateway worker only build the Event stream and call it.
- A change of model between two runs takes effect on the next send; nothing is invalidated.
- A Session is found by its log's id; the panel's twelve one-line delegations are gone.
- An Event kind is registered once, with its data keys and its narration. An unknown kind draws nothing in the chat and writes one logger line.
- The model list lives in a Qt-free module, `core/model_lists.py`; the provider manager keeps only the thread and the signal.

### Gateway
- An interactive Run asks its question again: the worker writes a `question` line and waits for the `answer`.
- An unattended run (no callback) never asks; an Owned Object deletion is refused and the agent finds another way.

### Lane and CLI
- The Lane takes `run_one` as a parameter, so its tests no longer patch the module.
- The Lane imports no FreeCAD. It decides the volume ratio and the base fallback from the result file `run.py` writes beside the saved STEP.
- One Task module opens a fixture for both `run.py --task` and the Lane.
- Lane Smoke on the integration branch, one validation task per Domain: Part, Assembly and BIM each finish with their Assertion run.

### Removed
- The Role table and everything behind it: `core/llm/roles.py`, `core/model_registry.py`, `core/role_defaults.py`, `settings_manager`.
- The sketch check and `Kernel.reading`.
- Five Tools nothing calls, and the review capture.
- The edit guard; the Part edit Assertion already checks same volume and same faces.
- The sheet fields `printed`, `drawn` and `between`.
- Event kinds nothing emits, and the panel's renderers for them.
- Tests that pinned only what is gone.

### Tests
- Suite: 1750 passed, 102 skipped (was 1897 passed, 106 skipped).
- `src` + `Mod`: 23 748 lines, now 19 929.
- A test that scans the tree derives its list instead of holding one.
- The model-list test no longer depends on test order.

### Knowledge base
- The decision "One model and one Effort per run; no Role table" is an ADR; *Roles and models* is rewritten.
- The Glossary entries Edit guard and Role read the new design.
- The Lane and `run.py` article describes `--task`.

Tickets: DAE-609 and its children DAE-593, DAE-610..DAE-622; ADR-0030.

## 1.3.0 — 2026-11-07

### Agent loop
- A Part Intent runs through the A4h agent loop in the addon and gives a PartDesign feature tree.
- The phased pipeline is deleted after an A/B test of its mechanisms; its Part rules go into the Part Domain prompt.
- An unattended run may rebuild what it created itself; the same refused deletion twice ends the run.
- A run records the model the provider answered with, beside the alias it asked for.
- An attachment the provider does not take reaches the model by its path.

### Assembly
- An Assembly Domain builds an Assembly with joints from parts in the Document or a STEP plus text.
- From text or a drawing set only, each part is built by a Part-Domain sub-agent and the parts are joined.
- An edit request changes an existing Assembly and the Assertion still passes; it deletes only what the Intent names, through the same approval BIM edit uses.

### BIM
- A floor plan (image/PDF) plus text gives native BIM objects (Wall, Slab, Door, Window, Space) for one flat, exported to IFC4.
- A floor plan plus text gives a multi-storey building with stairs.
- The BIM agent reads the walls off the plan into a table before it builds.
- A BIM edit request changes a Document with Arch objects, an IFC imported through `importIFC`, or native IFC through `nativeifc`.
- Generated IFCs carry fills, a storey and attributes in the file's unit.
- Furniture and equipment come from a whitelist of checked sources, else parametric Arch Equipment; generated only on explicit request.

### Panel and providers
- The panel shows the Domain inferred from the Intent and the Document; the user can override it.
- A run the panel gives its own stream emits every loop event, so the chat and the session log see the model work.
- A provider added by name on the Providers page lists its models and runs them.
- A model's output ceiling comes from its provider's own model list.

### Benchmark tooling
- Assembly benchmark: three modes (from parts, from scratch, edit), ~20 tasks, 70/30 split, deterministic scoring.
- BIM benchmark: two modes (create, edit), ~20 tasks, 70/30 split, deterministic scoring.
- Every Assembly edit task's input and reference pass the Assertion.
- `lane.py` never runs a paid task without a ceiling and runs each task at most the attempts it was given.
- From a git worktree, the lane and the scorer use the main checkout's test data and harness venv.

### Tests
- A crashed pytest-xdist worker fails the run instead of hanging it.
- No test writes files into the repository root; each test leaves the scratch settings as it found them.

### Docs
- The project documentation lives in the YouTrack knowledge base; the README keeps install, tests and scope.

## 1.2.0 — 2026-10-25

### Building from a drawing
- Design goes back to the drawing for a second pass before it asks a person, in headless and interactive runs.
- Design reads each dimension from two views and keeps a dimension, value and view table; it states axes and faces per view and cross-checks the views before Plan.
- Design builds the part the isometric shows; a flat pattern only shows how the sheet is cut. Plan and Execute build a bent sheet part as a pad per wall and a revolve per bend.
- Design keeps an intermediate state out of the envelope; Plan sends an unbuildable feature back to Design.
- Plan keeps question as an option in both modes; decide-and-assume is the last resort.
- A Plan value with text in it stays usable by its Steps.
- A Step whose Body does not survive a STEP round trip is refused, with the bad faces named.
- A reply is remembered as the object it carries.

### Editing an existing part
- Edit Design locates the exact region and dimension in the input STEP before Plan.
- Edit Plan counts every affected feature and its extent; Review checks it; unrequested changes are refused.
- A step address that is not a real face is asked again, with the nearest real addresses listed.

### Checks
- Acceptance counts come from Design's own feature list and are counted the way the check counts.
- The through-feature check counts features.
- A revision may dispute an unmet check with a measurement; the run ends unverified, and blocked only when it does not.

### Benchmark tooling
- Drawing sheet generator for complex ABC parts from STEP (70 test / 30 validation): view layout and scale, section and detail views, ISO 128 line weights, running dimensions with overlap-free placement, symmetry, equal-spacing and concentric notes.

### Tests
- The full pytest run finishes and is green: hanging, flaky and redundant tests are deleted.

## 1.1.0 — 2026-10-14

### Editing an existing part
- An editing run starts from the input STEP as the base of the Body; Design gets a text inventory of the part and its face addresses.
- Plan and Execute change the base solid without rebuilding it; edit assertions check the change and its collateral.
- An invalid or non-manifold imported base is repaired when the run starts.
- A changed feature carries the base-part addresses it changes; Preflight checks the changes against the Design.
- An edit whose Design still carries a refused marker stops as blocked before Plan.
- Body-level booleans are allowed in edit Steps; helper objects are no longer exported beside the edited Body.
- The self-intersection check no longer times out on edited parts.

### Building from a drawing
- A non-manifold STEP is caught after the Step that made it and repaired before export.
- A fillet, chamfer or draft planned before a pocket, hole, pattern or thickness is refused unless the Step says why.
- A single-sheet drawing never loses a value to a later page; a Step citing an address with no number is refused.
- When Thickness cannot be built, Plan gets an alternative construction of the same wall.
- Plan bookkeeping complaints are patches, not rewrites; each Step has its own turn share; a headless phase asks at most three questions.
- Review and Design read intent and outcome from the kernel namespace.

### Runs and providers
- Effort `xhigh` is offered on every route.
- A provider quota refusal is not retried at once; a run with no solid writes no STEP and exits with its own code.

### Service (home server)
- Gateway + worker stack: headless FreeCAD 1.1.4 worker image, Postgres store, Runs API, SSE events with resume, cancel and answer, artifacts as blobs, Session queueing/idle TTL/respawn, bearer-token auth, one `compose.yaml`.

### Benchmark tooling
- The lane runs CADGenBench editing fixtures; the submission zip includes every editing fixture.
