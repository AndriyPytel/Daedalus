# FreeCAD Copilot

An assistant living inside FreeCAD's own UI that builds and edits parametric models on the user's behalf, while leaving the user free to take the wheel at any moment.

## Language

### What the user asks for

**Intent**:
What the user wants changed about the model, expressed in their own words.
_Avoid_: prompt, request, query

**Plan**:
An ordered, revisable list of Steps produced from an Intent, shown to the user before anything touches the model.
_Avoid_: task list, todo, strategy

**Step**:
One unit of change to the Document, small enough to be attempted, judged and undone on its own.
_Avoid_: task, action, operation

**Plan Gate**:
The single point at which the user approves a Plan. Approval covers every Step in it; there is no per-Step approval.
_Avoid_: confirmation, permission prompt

### The model

**Document**:
The FreeCAD document. The sole source of truth for the model's state — including changes the user made by hand.
_Avoid_: model, file, project, scene

**Snapshot**:
The cheap summary of Document state handed to the assistant on every Step: what objects exist, what is active, what the user has selected. Never the whole document.
_Avoid_: context dump, state, tree

**Owned Object**:
An object the assistant created during the current Session. Anything else belongs to the user and may not be modified or deleted without asking.
_Avoid_: our object, generated object

**Computed Reference**:
A way of pointing at geometry that is worked out at the moment of execution rather than written down in advance. The alternative — a fixed name like `Edge12` — silently points elsewhere once a parameter upstream changes.
_Avoid_: element name, topological reference, selector

### Doing the work

**Tool**:
The only sanctioned way to read or change the Document. Anything the assistant can do to the model, it does through a Tool.
_Avoid_: function, command, API call

**Preflight**:
Running a Step against a throwaway copy of the Document before the real one sees it. A Step that fails Preflight never reaches the user's Document.
_Avoid_: dry run, validation, simulation

**Preflight Worker**:
The isolated, disposable process where Preflight happens. Its death is a normal outcome, not an incident.
_Avoid_: sandbox, executor, runner

**Apply**:
Committing a Step that survived Preflight to the real Document, as one undoable unit.
_Avoid_: execute, commit, run

**Review**:
The independent judgement at the end of a Plan on whether the result matches the Intent. Sees the Intent and the outcome; deliberately never sees how the outcome was produced.
_Avoid_: verification, QA, check

**Session**:
One continuous stretch of work on one Document, and the scope over which Owned Objects are remembered.
_Avoid_: conversation, thread, chat

**Refusal**:
The provider declining to answer — an exhausted quota, a rate limit, a rejected key, a request past the context window, a reply cut off at the output cap, a dropped connection. Not a bad reply: the model was never allowed to answer, so the run stops blocked with the provider's own reason rather than erroring.
_Avoid_: API error, exception, malformed reply
