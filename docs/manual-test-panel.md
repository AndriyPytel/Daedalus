# Manual test script — the Qt panel

The panel is the one layer with no automated tests. That is a decision, not an
oversight: driving Qt in CI costs more than it catches, and a GUI `QApplication`
in a headless test process takes the whole suite down with it. So the panel is
checked **deliberately, from this script**, rather than from memory.

Run it before a release, and after any change to `ui/`.

Record the result at the bottom. A run with no recorded result did not happen.

## Setup

| | |
|---|---|
| Build | FreeCAD 1.0 or later, GUI (not `freecadcmd`) |
| Provider | An API key configured in **Preferences → FreeCadCopilot → API & Providers** |
| Document | A new, empty document, saved once so it has a path |
| Report view | Open (**View → Panels → Report view**) — errors land there |

Do not start from a document containing work you care about. Step 9 asks the
assistant to modify an object you made by hand.

## The script

Each step has an **expected observation**. If what you see differs, that is a
finding — write it down verbatim rather than deciding whether it matters.

### 1. The workbench loads

Select **FreeCad Copilot** from the workbench dropdown.

> **Expect:** the Copilot panel docks on the right. The Report view shows
> `[SUCCESS] CopilotWorkbench loaded into FreeCAD GUI.` and no traceback.

### 2. The model picker is populated

Look at the pill next to the paperclip in the input card.

> **Expect:** it names a real model, not "No models — add API key". Changing it
> and sending again uses the new model.

### 3. An Intent produces streaming reasoning

Type: `Make a 10mm cube and fillet its top edges with a 1.5mm radius`
and press the send button.

> **Expect:** within a second or two, a collapsed **"Thinking (plan)…"** row
> appears and pulses, and the status line under the transcript fills with
> reasoning text that **keeps changing as it arrives** — not one block appearing
> at the end. Click the row: it expands to the full reasoning.

### 4. FreeCAD stays usable while it thinks

While reasoning is still streaming, orbit the 3D view and open a menu.

> **Expect:** the view orbits smoothly and the menu opens. Nothing is frozen,
> no spinning beachball / hourglass.

### 5. The Plan renders as a list, and nothing has run

> **Expect:** a **Plan** card listing the Steps, each `N. ○ <description>`, all
> grey. Below it a card reading **"Run this Plan? Approving covers all N Steps
> — you will not be asked again."** with **✓ Approve / ✎ Amend / ✗ Reject**.
>
> **Critically:** the model tree is still empty. Nothing has been created.

### 6. Amending a Plan

Type `Use PartDesign, not Part primitives` in the input box and press **✎ Amend**.

> **Expect:** your text appears in the transcript as a message, then a
> **"Revised the Plan: You asked for: Use PartDesign, not Part primitives"**
> line, then a new **Revised Plan** card, then a fresh gate card. The model tree
> is *still* empty.

(Pressing **Amend** with an empty box should not send — the placeholder changes
to "Say what to change, then press Amend…" instead.)

### 7. Approve, and watch the Steps advance

Press **✓ Approve**.

> **Expect, in order:**
> - the gate card greys out and cannot be clicked again;
> - the status line reads `Step 1/N: <description>`;
> - in the Plan card, Step 1 turns **blue and bold with a ▸**;
> - a code block appears with the Python for that Step;
> - a line `✓ Applied "<label>" — <objects>. Ctrl+Z undoes this Step.`;
> - an isometric render of the model so far;
> - Step 1 turns to `✓`, Step 2 turns blue, and so on.
>
> Geometry appears in the 3D view as each Step lands. You are **not** asked to
> approve any individual Step.

### 8. A Preflight failure and the retry

This one you cannot force directly — it happens when the model writes code that
does not work. If it occurs during any run:

> **Expect:** a row **"✗ Preflight failed (attempt N) — retrying"**. Expanding
> it shows the traceback or the lint reason. A new code block follows, then a
> successful `✓ Applied`.
>
> **Critically:** nothing appeared in the 3D view for the failed attempt. The
> failure cost you nothing.

To force it deliberately: ask for something using a removed API, e.g.
`Set the sketch's Support property to the XY plane`.

> **Expect:** the failure names `Support`, and the retry uses
> `AttachmentSupport`.

### 9. The Owned Object rule

Now, **by hand**, create a box: switch to the **Part** workbench and use
**Part → Primitives → Cube**. Switch back to Copilot.

Ask: `Make the box 50mm long`

> **Expect:** a modal dialog, **"Your own objects"**, saying the assistant wants
> to *modify Box*, and noting it did not create it this session. The default
> button is **No**.
>
> Press **No.** → the transcript says you refused, the box is **unchanged**, and
> the assistant either tries another way or stops. It does not modify the box.
>
> Ask again and press **Yes.** → the box changes to 50mm, and one Ctrl+Z undoes
> it.
>
> Then ask `Make it 60mm long`. **Expect to be asked again** — saying yes once
> permits one change, it does not adopt the object.

### 10. Ctrl+Z undoes a whole Step

Open **Edit → Undo** and look at the entry name, then press Ctrl+Z once.

> **Expect:** the undo entry is named after the Step (e.g. *"Fillet the top
> edges"*), not "AI Generated Feature". One Ctrl+Z removes **everything that
> Step did** — even if it created several objects — and leaves the previous
> Steps in place.

### 11. Stopping mid-run

Start a new, longer Intent (`Make a 100x50x20 bracket with four mounting
holes`). Approve it. While Step 2 or 3 is running, press **■ Stop**.

> **Expect:** the status line reads "Stopping after the current attempt…", then
> the run ends with **⏸ Stopped by you. Everything applied so far is still in
> the Document; undo it Step by Step if you want it gone.**
>
> The Steps already applied are still there. No half-applied Step, no error
> dialog, no frozen panel. The send button works again.

Also: press **Stop** while the **gate card** is showing.

> **Expect:** the run ends the same way rather than waiting forever for an
> answer.

### 12. Rejecting a Plan

Send another Intent and press **✗ Reject** at the gate.

> **Expect:** **⏸ You rejected the Plan, so nothing was applied.** The model
> tree is unchanged.

### 13. The Review verdict and its render

At the end of a successful run:

> **Expect:** the last render shown is the one Review judged by (hovering it
> shows the file path), followed by
> **✓ Review: pass — <the reviewer's reason>**. On a failure it reads
> **✗ Review: fail — …** and a Revised Plan follows.

### 14. The session log

Find the log directory (`Preferences → FreeCadCopilot`, or the path printed in
the Report view at startup) and open the newest `*.events.jsonl`.

> **Expect:** one JSON object per line, in the order things happened, with a
> `kind`, an `at` timestamp and a `data` object. Every event you saw in the
> panel is there — including the tracebacks — so the run can be read back
> without repeating it.

### 15. Nothing crashed

> **Expect:** the Report view contains no Python traceback from the addon, and
> FreeCAD has not crashed at any point. Any generated code that would have
> crashed took a Preflight Worker with it instead, silently.

---

## Result

Copy this block, fill it in, and leave the previous runs above it.

```
Date:
FreeCAD version:
Provider / model:
Tester:

  1. workbench loads                    [ ]
  2. model picker populated             [ ]
  3. reasoning streams as it forms      [ ]
  4. FreeCAD stays usable               [ ]
  5. Plan renders, nothing has run      [ ]
  6. amending a Plan                    [ ]
  7. Steps advance, one approval        [ ]
  8. Preflight failure and retry        [ ]
  9. Owned Object rule, no then yes     [ ]
 10. Ctrl+Z undoes a whole Step         [ ]
 11. stopping mid-run, and at the gate  [ ]
 12. rejecting a Plan                   [ ]
 13. Review verdict and its render      [ ]
 14. session log readable               [ ]
 15. nothing crashed                    [ ]

Findings:
```

### Run 1 — 2026-07-26

```
Date:            2026-07-26
FreeCAD version: 1.0.2 (39319)
Provider / model: none — no API key available in this environment
Tester:          Claude (agent), offscreen (QT_QPA_PLATFORM=offscreen)

  1. workbench loads                    [~] panel constructed and rendered; workbench
                                            registration itself needs a GUI FreeCAD
  2. model picker populated             [~] shows the configured model, not exercised
                                            against a live provider
  3. reasoning streams as it forms      [~] per-chunk `thought` events verified in
                                            tests/test_loop.py; not seen with a live model
  4. FreeCAD stays usable               [~] loop confirmed running off the GUI thread;
                                            not judged by hand
  5. Plan renders, nothing has run      [P] Plan card + gate card rendered, and the live
                                            Document held 0 objects at the gate
  6. amending a Plan                    [ ] not exercised offscreen
  7. Steps advance, one approval        [P] 2 Steps applied after one approval; Plan card
                                            showed 1.✓ / 2.▸ / 3.○ correctly
  8. Preflight failure and retry        [P] rendered from a synthetic run: the failure row,
                                            the traceback and the following retry
  9. Owned Object rule, no then yes     [P] covered at Level 2 against a real Document
                                            (refuse -> unchanged, allow -> changed); the
                                            modal itself not clicked by hand
 10. Ctrl+Z undoes a whole Step         [P] Level 2: one undo removed a 3-object Step, and
                                            each Step of the slice undid in turn
 11. stopping mid-run, and at the gate  [~] covered at Level 1; buttons not clicked by hand
 12. rejecting a Plan                   [~] covered at Level 1; button not clicked by hand
 13. Review verdict and its render      [P] verdict line and render both rendered
 14. session log readable               [P] one JSON object per line, replayed in order
 15. nothing crashed                    [P] no addon traceback; FreeCAD survived a
                                            deliberate segfault in generated code

Legend: [P] passed  [~] partially / by proxy  [ ] not run  [F] failed

Findings:
  - Step 1 of the slice leaves only a sketch, and a wire has no facets, so that
    Step produces no render. Harmless (Review judges the finished model) but the
    transcript has a gap where a picture would be. Worth an edges-only fallback
    in the rasteriser if it reads as a bug to users.
  - A headless `newDocument` has `UndoMode = 0`, so undo silently does nothing
    while the panel says "Ctrl+Z undoes this Step". The GUI sets it, so this
    should not bite a real user — but the pipeline does not check it.
  - The render of the finished slice shows the fillets correctly, but one of the
    curved fillet strips has a speckled band where the painter's-algorithm sort
    picks the wrong triangle order. Cosmetic, and it did not obscure the shape,
    but it is the known ceiling of the software rasteriser (marked `ponytail:` in
    core/preflight_worker.py). Upgrade path is a per-pixel depth test.
  - THIS RUN IS NOT A SUBSTITUTE FOR A HUMAN RUN. Steps 6, 11 and 12 need a
    person clicking buttons, and every step needs a live model. Re-run in the
    FreeCAD GUI with an API key before calling the slice done.
```
