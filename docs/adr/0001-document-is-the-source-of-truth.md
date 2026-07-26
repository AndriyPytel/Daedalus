# The Document is the source of truth, not a generated script

Systems that generate CAD from LLMs usually treat a parametric script as the source of truth and the CAD document as a build artifact, which makes edits cheap and replayable. We rejected that: this is a Copilot, so the user must be able to edit the model by hand between Steps and have those edits be real. A script-as-truth design would either ignore hand edits or maintain a second history alongside FreeCAD's own feature tree, and the two would drift.

## Consequences

- Execution must happen inside FreeCAD's process, on the Qt main thread. Running the agent loop headless in a worker was therefore never available as the primary execution model — see ADR-0002.
- Each Step is wrapped in a FreeCAD document transaction, so the user's Ctrl+Z undoes a whole Step rather than half a feature.
- Editing an imported or hand-built model needs no special mode: it is just a Document like any other.
