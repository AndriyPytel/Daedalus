# Changelog

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
