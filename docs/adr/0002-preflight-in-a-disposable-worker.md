# Every Step is preflighted in a disposable worker before it touches the Document

Because the Document is the source of truth (ADR-0001), generated code runs in FreeCAD's own process — where a bad call into the C++ core is a segfault, not a Python exception, and takes the user's unsaved work with it. So no Step reaches the real Document until it has already run to completion against a throwaway copy in a separate, killable process.

## Consequences

- The Preflight Worker dying is an expected outcome and the signal the design exists to produce. It restarts silently; it is not surfaced as a user-facing error.
- The worker is long-lived and warm, but reloads the Document copy for every Preflight. Keeping a mirrored in-memory state would be faster and would eventually diverge from the live Document — at which point the only safety net in the system would start quietly approving code that crashes. Reload cost is the price of that guarantee.
- Rendering an image of the result costs nothing extra here, so Review's visual check is a by-product of Preflight rather than a separate subsystem.
- Generated code must be a pure function of the Document: no `Gui.*`, no reliance on live selection. Otherwise Preflight and Apply can disagree.
