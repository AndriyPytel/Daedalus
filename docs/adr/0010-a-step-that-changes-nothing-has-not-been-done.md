# A Step that changes nothing has not been done

A Step whose code raises is a failure the assistant corrects against. A Step whose code runs cleanly and touches nothing used to be scored as a success — and that is worse, because the Plan moves on believing an object exists.

It happened: Step 2 of a twelve-Step plan replied with a tidy `def add_sketch_to_xy_plane(doc, app):` and never called it. Preflight ran it without error, the pipeline reported applied, and the Step landed as `created: []`. The function body contained `origin.Origins` and `sketch.Support` — one invented, one removed in FreeCAD 1.0 — and neither ever executed, so neither was ever caught. The next Step then had to build the sketch that Step 2 was credited with.

The observation needed to catch this already existed. `_Touched` watches the document observer and reports what the Step created, deleted and modified; nothing was reading it as a verdict. Now a Preflight that touched nothing fails, with a message that names the likely cause and points at `{"obsolete": "..."}` for the case where the Step genuinely has nothing left to do.

The check runs on the Preflight result, so a no-op Step never opens a transaction against the real Document.

## Consequences

- A Step that sets a property to the value it already holds emits no change and now reads as a no-op. It costs an attempt, and the assistant's way out is `obsolete`. Accepted: it is rare, and the alternative is trusting a Step that demonstrably did nothing.
- Plan was rewriting one modelling operation as three or four Steps — "create the sketch", "attach it to XY", "draw the circle", "close the sketch". A twelve-Step plan for a gear is twelve blind generations instead of four, and the GUI-flavoured ones ("close the sketch") have no code that could change anything, so they could only ever be no-ops. The Plan prompt now asks for whole operations, which is also what makes this rule safe to enforce.
- The fake worker in the pipeline tests had to start reporting what it touched. A fake that always claims success while reporting nothing is exactly the lie ADR-0007 is about.
