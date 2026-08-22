# Diagnosis: replan ignores what is already built

Session used as evidence: `logs/sessions/cd594cb0281244f7b366d94dd8740714.jsonl`
(85 events). Confirmed against the code, not just the parent's summary.

## a) Where the rewritten plan is produced

`core/loop.py:2862-2886` — `rewrite(cause, refused, feedback, tried, reason=None)`,
nested in `_run`. Every one of the seven replan causes funnels through it
(`core/loop.py:120-146`, `_Revisions`). It calls `replan(feedback,
revising=list(steps))` at `core/loop.py:2880`, where `steps` (`core/loop.py:2889`,
`steps = planned.steps`) is the **full** Step list of the Plan just attempted,
not a filtered "still to do" subset. `replan` (`core/loop.py:2811-2814`) calls
`_plan_once_it_knows_enough` -> `plan()` (`core/loop.py:708`), which does read a
fresh snapshot each time (`tools.call("snapshot")` at `core/loop.py:2814`).

In this session: Steps 1-7 of an 8-step Plan applied (ev24-51, real objects
created: `Sketch_Outer`+`Pad_Outer` ... `Fillet_R2`). Step 8 (assign material)
failed 3 times (ev55, ev63, ev67 — `attempts_per_step: int = 3`,
`core/loop.py:101`) and handed back (`core/loop.py:3196-3204`). This became
cause `UNBUILDABLE` (`core/loop.py:2963`) and `rewrite()` fired at ev68-71.

## b) What state is and is not seen

**Seen:** a fresh Document `snapshot` (`core/freecad_tools.py:622-635`, returns
object names/labels/types/errors) goes into every `plan()` call as `document`
(`core/loop.py:748`) and every `execute()` call as `document`
(`core/loop.py:1049`). The prior Plan's full Step text goes in as
`the_plan_you_wrote` (`core/loop.py:750`).

**Not seen, anywhere:** which Steps of that prior Plan actually landed. `progress`
(`core/loop.py:1791`, `{"applied": 0, "tried": [], "unmet": ..., "satisfied":
[]}`) tracks only a *count* and the *addresses* satisfied — never which Step
indices or object names were already created. Nothing is handed to Plan or to
Execute that says "Steps 1-7 already exist in the Document, only Step 8 is
open."

The prompts pull opposite ways here. `config/prompts/plan.md:96-98`:
**"`the_plan_you_wrote` is yours, and a revision amends it. Send back every
Step of it, in order, with the fix applied and whatever is missing added"** —
tells the model to *restate every Step*, applied or not (written to stop it
dropping *unbuilt* Steps, FCC-148, `core/loop.py:887-894`; it does not
distinguish applied from unbuilt). `config/prompts/execute.md:119-122`: **"A
Document that already has the part in it is a part to edit, not a site to
clear... change the object that holds it"** — tells Execute not to
delete-and-rebuild, but still requires it to touch/regenerate code for the
already-correct object. Neither prompt says "if this Step's objects already
exist and are error-free, leave it alone." The model must reconstruct which
Steps are done by diffing prose against the snapshot's object list, on every
replan.

## c) The log trace

- ev13 22:17:44 `plan`, 8 Steps.
- ev24-51: Steps 1-7 `applied` (real `created` lists per event, e.g. ev27
  `created: ["Sketch_Outer", "Pad_Outer"]`).
- ev55/63/67: Step 8 fails 3x (`RuntimeError: no material card named plain
  carbon steel`, then two empty replies).
- ev68-71 22:21:10-22:21:33: `plan_revised`, **8 Steps again**; Step 1 text is
  near-identical to the original Step 1 ("In the existing Body, sketch the
  outer obround profile on the XZ plane...").
- ev77 22:24:58: `step_started`, index 1 of 8 — the loop is re-executing
  from Step 1, mechanically confirmed at `core/loop.py:2946`
  (`_work_through(steps, ...)` called with the full new `steps`, no offset),
  `core/loop.py:3130` (`index = 0`), and `core/loop.py:1047-1049`
  (`execute()`'s default `start=0` slices `steps[0:]`, the whole list).
- ev78: the generated code for "new" Step 1 is defensive
  (`sk = doc.getObject('Sketch_Outer'); if sk is None: ...`, reusing the
  existing `Sketch_Outer`/`Pad_Outer`) — proof Execute *did* read the
  snapshot and tried to comply with "edit, don't clear". It also adds a line
  absent from the original successful code: `pad.SideType = 'Dimension' if
  hasattr(pad,'SideType') else pad.SideType`.
- ev79/80: `ValueError: 'Dimension' is not part of the enumeration in
  preflight_6_8qpvz_#Pad_Outer.SideType`. The original Step-1 code
  (`core/loop.py` ev25, first attempt) never touched `SideType` at all and
  built cleanly; the regenerated code invents an enum value that does not
  exist and breaks the already-good `Pad_Outer`.
- ev84 22:26:27: `blocked` — run stops, budget exhausted on a Step that had
  already succeeded once.

## d) The concrete failure in the pipeline

Not a name collision in `core/apply.py` — `apply_step` (`core/apply.py`)
passes code through Preflight/OCC unchanged and just relays whatever
traceback comes back (`core/apply.py:31-36`, `ApplyResult.as_tool_result`).
The actual break is upstream, in code *generation*: forced to regenerate Step
1 from nothing (no record that it already worked), Execute writes new,
untested code against a live object it has never edited before, and invents
an invalid `SideType` value. `_FAILED_THIS_WAY` (`core/loop.py:1105-1130`)
even carries a directive for exactly this traceback shape ("is not part of
the enumeration" -> "ask `describe_type`... guessing a second spelling is the
same move that failed") but that only fires on a *second* identical failure
at the same Step (`core/loop.py:1130-1150`), and the run had no more
attempts/revision budget left by ev80.

## e) Smallest fix, and its test

`core/loop.py:2946` and `core/loop.py:3130/3134`: `_work_through`/`execute`
have no notion of "Steps already applied against this Document" carried
across a `rewrite()`. The surgical fix is to track, per applied Step, what it
built (`progress["applied_steps"]`, extending the existing `progress` dict at
`core/loop.py:1791` with created-object names, alongside the existing
`progress["satisfied"]`), and before calling `_work_through` on a revised
Plan, compute how many of its leading Steps already correspond 1:1 (by
`satisfies` address set, which is already tracked per Step — `cites` in
`_steps_of`, `core/loop.py:806-826`) to Steps already applied, then pass that
count as `_work_through`'s starting index / `execute`'s `start=`
(`core/loop.py:1046-1048`), so Execute is *never asked to write code again*
for a Step whose objects already exist untouched.

Existing tests only exercise the happy path where the mocked LLM revision
*already* drops applied Steps —
`tests/test_loop.py:3403` (`test_the_replan_after_a_handback_reads_the_document_as_it_is`)
and `tests/test_loop.py:5031`
(`test_a_revision_that_plans_only_the_unbuilt_steps_passes_the_coverage_check`)
both script the mocked Plan reply to omit applied Steps — they do not cover a
revision that *restates* already-applied Steps, which `config/prompts/plan.md:96-98`
explicitly licenses. The fix belongs in `tests/test_loop.py`, as a new case
alongside `_four_steps_applied_then_a_handback` (`tests/test_loop.py:5009`)
where the mocked revised Plan **echoes back** the already-applied Steps
verbatim (as the real Opus run did) and the test asserts
`tools.applied_codes()` does *not* contain a second `apply_step` call for
those Steps.

Speculation, flagged: whether `'Dimension'` was ever a valid `PartDesign::Pad
SideType` enum value in FreeCAD 1.0 is not verified here (no FreeCAD run was
done, per the task constraints); the evidence only establishes that the
original, successful Step-1 code never set it and the regenerated code did,
and that regeneration is what introduced the break.
