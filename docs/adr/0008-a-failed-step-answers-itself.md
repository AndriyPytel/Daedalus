# A failed Step answers itself, and only unanswerable failures become lint rules

An `AttributeError` out of FreeCAD names the attribute that is missing and never the ones that exist. That is a dead end: the assistant has nowhere to go but a second guess at the same wrong idea, and the Step budget goes on one misconception. Observed twice — three attempts spent on spellings of "make this Body active", then three more on `body.Origin.XY_Plane`, where attempts two and three were the same code with different comments.

The tempting fix is a lint rule per surprise. That list is infinite and always one release behind. Instead the Preflight Worker answers the error with introspection at the moment the guess fails: `AttributeError.obj` and `.name` survive FreeCAD's C++ bindings, so the failure comes back carrying the object's real `TypeId` and its `PropertiesList`. This is ADR-0006 applied where the assistant is already looking, rather than hoping it thinks to ask first.

That fixes the family, not the instance — `body.Origin.XY_Plane` now returns `App::Origin has no 'XY_Plane'. It has: … OriginFeatures …`, and every future removed-or-renamed property gets the same treatment for free.

## Where the boundary sits

A failure belongs in the lint only when the traceback **cannot** teach the way out:

- **Nothing downstream can see it.** A literal `Edge12` does not raise; the fillet lands on the wrong edge and the model quietly deforms (ADR-0005).
- **Preflight and Apply would disagree.** `Gui.*` and live selection make the two processes run different programs (ADR-0002).
- **No correct spelling exists.** Activation (`ActiveObject`, `setActiveObject`, `activeView`) is GUI-only, so there is no code the assistant could converge on — introspection would just confirm the absence, once per spelling.

Everything else is a traceback's job. If a new failure mode is answerable by looking at the object, it does not get a rule.

## Consequences

- The hint is `PropertiesList` where the object has one, and public `dir()` otherwise — 6 useful names instead of 84 mostly-plumbing ones. Capped, and `difflib` names the closest existing attribute when the miss is a mere misspelling.
- Building the hint is wrapped: a hint is never worth losing the traceback it was meant to explain.
- The prompt's trap list (`TRAPS`) stays short by construction. A trap earns its place only when introspection would report something that exists but is wrong to use — not when it would simply report the right answer.
- Level 2 covers this, since a mock would happily agree that `XY_Plane` exists (ADR-0007).
