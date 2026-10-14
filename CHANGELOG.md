# Changelog

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
