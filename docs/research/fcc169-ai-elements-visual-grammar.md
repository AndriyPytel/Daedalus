# AI Elements — Visual Grammar for the FreeCAD Copilot Panel

Extracted from the Figma file `AI-Elements ▲ Vercel (Community) (Copy)` (key
`1PvKcQ3bmAbgMPHdkncOfl`, 51 pages, 63,252 nodes), read through the local
offline index at `~/.cache/figma/1PvKcQ3bmAbgMPHdkncOfl.db` / `.full.json`
via `~/bin/figma` and direct SQLite/JSON queries — no network calls were
spent. The second file key given in the task, `kLFY8VhDTK4tuO6FMx0KUs`, has
**no local snapshot** (only `comments.json`/`versions.json` exist for it) and
the Tier-1 budget is currently over quota (7/6 used, next slot frees
2026-09-11), so it was **not** queried; everything below comes from the
`1PvKcQ3bmAbgMPHdkncOfl` copy, cross-checked against the prior extraction at
`docs/research/fcc167-ai-elements-figma-spec.md`.

Per ADR-0011 / FCC-A-16: no hex values are given. Every fill/border below is
reported as a *role* (surface, hairline border, hover wash, primary fill,
none) — map roles onto `rgba(127,127,127,a)` greys and `palette(Highlight)`
in the implementation.

Node ids are Figma node ids in the file above; `I<a>;<b>;...` ids are
instance-override paths (component-instance nesting), not separate
components.

---

## A. THE GRAMMAR

### A1. Spacing scale

Base unit is 4px. Across the whole file `gap=8` is the single most common
value (12,701 occurrences vs 4,478 for `gap=4` and 655 for `gap=16`) — 8px is
the default "things belong together" gap; everything else is a deliberate
exception.

| value | role | evidence |
|---|---|---|
| 2px | vertical padding inside a status pill | Badge `2116:34457;2009:20859;2006:15585` pad `2,8,2,8` |
| 4px | gap inside a tight icon+label cluster; padding of a 24px icon-only button | QueueItemAction `4035:18744` pad `4,4,4,4`; QueueItem row gap `4035:18528` gap 4; Suggestion icon-to-label gap |
| 6px | vertical padding of a file-name chip | TaskItemFile `2005:15373` pad `6,8,6,8`(L/R 8) |
| 8px | **default gap** — icon-to-text, row-to-row inside a list, button toolbar gap | ToolInput label→body gap `2011:2422`; PromptInputFooter gap `4768:192408`; ConfirmationActions gap `4346:15375`; Task list item gap `2005:15411` |
| 12px | inline padding of a compact editable surface | PromptInput Textarea `4768:192406` pad `12,12,12,12`; user MessageContent bubble vertical pad `4371:14517` (`12,16,12,16`) |
| 16px | card/block padding; panel gutter; gap **between** blocks, not within one | ChainOfThoughtStep/ChainOfThoughtHeader/ToolInput/ToolOutput pad `16,16,16,16`; Conversation container `2204:4792` pad `16,16,16,16` **and** gap 16 between message turns; Reasoning trigger→content gap `2008:16170` = 16 |
| 24/32/48px | page-level / modal spacing | ModelSelectorDialog `4451:14875` pad `48,16,48,16` — a full-screen modal chrome, **not relevant at 400px** |

Practical read for the dock: **chip = 2-6px vertical / 8px horizontal**,
**list row gap = 8px**, **card padding = 16px** (12px for the one compact
exception, the composer textarea), **gutter around the whole panel = 16px**,
**gap between conversation turns = 16px**, **gap within one message's
sub-blocks (Sources→Reasoning→Content→Toolbar) = 0px explicit** — each
sub-block carries its own internal padding instead of the parent adding a
gap (see A4, Density).

### A2. Radius scale — the rule is "what kind of thing is this"

| radius | role | evidence |
|---|---|---|
| 9999 (full pill) | a **label**, not a container: status badges, the queue-item status dot | Badge `...2006:15585`; QueueItemIndicator `4035:18526` |
| 6-8px | an **action or a tight chip**: icon buttons, chip-shaped attachments/file tags, list rows on hover, the tool-call card | QueueItemFile 6 (`4035:20037`); TaskItemFile 8 (`2005:15373`); MessageAction Button 8 (`2162:9316`); Confirmation buttons 8 (`4346:15370/71`); PromptInputSubmit 8; QueueItem row 8 (`4035:18528`); Tool card 8 (`2006:15537`) |
| 10px | a **surface/card**: message bubble, composer, results panel, popover trigger chip | user MessageContent bubble 10 (`4371:14517`); PromptInput 10 (`4768:192397/403/415`); TestResults 10 (`4593:4186`); ContextContent popover 10 (`2162:13731`); ContextTrigger usage-pill 10 |
| 14px | a **large floating container**: the model-picker dropdown | ModelSelectorContent 14 (`4369:183920`) |

Rule for the implementer: **8px is the default control radius** (any button,
icon-button, tag, or the tool card get 8). **10px is the default surface
radius** (any card the user reads as a distinct panel — message bubble,
composer, results block, popover). Reserve **9999 (pill)** strictly for
labels that carry a status word, never for a container that holds other
content. 14px only if something floats above the whole panel (a dropdown) —
skip it in-panel.

### A3. Type scale — size/weight pairs only (family/colour = theme)

| pair | line-height | role |
|---|---|---|
| 12 / 400 | 16px | caption / muted numeric meta |
| 12 / 500 | 16px | small label — a card's section caption (`Parameters`, `Result`) **or** a chip's own text (QueueItemFile, TaskItemFile) depending on whether it's muted or full-emphasis |
| 14 / 400 | 20px | **body** — message text, reasoning-trail prose, chain-of-thought descriptions, a header title that is *not* meant to stand out (`Chain of Thought` label itself is 14/400) |
| 14 / 500 | 20px | **emphasis body** — a value or identity string sitting inside a body-sized line: tool name in ToolHeader, `AlertTitle` in Confirmation, the usage `%` in the context pill, `TestSuiteHeader`'s title |
| ~13 / 500 | — | toolbar-button labels (`Search`, `GPT-4o` in the composer footer) — a scaled instance of the 12/500 label style, not a separate tier |

Nothing above 14/500 appears inside any in-panel component; 16-30px sizes
exist only on the Demo/marketing pages and do not apply to a 400px dock.
**Rule:** a heading in this system is never a bigger font — it's 14/500
instead of 14/400 in the same line-height. Size differentiates only
control-scale (12 vs 14), not hierarchy.

### A4. Density

- Line-height is consistently **1.43×** at 14px (20px) and **1.33×** at 12px
  (16px) — tight but not cramped.
- A **text-only interactive row** (TaskItem, a Sources entry) is 20px tall —
  exactly one line-height, no extra padding.
- A **control row** (icon button, chip button) is **28px tall** — this is by
  far the dominant control height in the file (1,280 `Button` instances are
  28×28, next most common is 32×32 for header-toolbar triggers). QueueItem's
  icon-only actions are the one exception at 24×24 (denser context, inside
  an already-dense list).
- A **two-line list row** (QueueItem: content line + description line) is
  52px: `4px pad + 24px content row + 16px description row + 4px pad`
  (padding is 12/4, not 16/16 — queue rows are the densest list in the
  system).
- Inside one assistant message, `Sources → Reasoning → MessageContent →
  MessageToolbar` stack with **zero added gap** — each block's own height
  already includes its breathing room (e.g. `Reasoning` closed row is 36px
  tall for content that measures 20px — the extra 16px is the block's own
  top/bottom pad). Do not add a spacer between these sub-blocks; size them
  correctly instead.
- Between separate **conversation turns**, the gap is an explicit 16px
  (`Conversation` container, state=initial, `2204:4792`).

### A5. Separation strategy — the single most important question

AI Elements uses exactly **five** ways to separate content, ranked from
"nothing" to "loudest," and picks the cheapest one that still reads:

1. **Nothing** — no fill, no border, separation by spacing/typography alone.
   Used for: assistant message text itself (no bubble — `4371:14540` has
   *no* fill, *no* padding, text sits flush), TaskItem rows, Reasoning /
   ReasoningTrigger / ChainOfThoughtStep's own variant frame (relies on the
   parent card for its edge), a ghost/default-state icon button
   (`MessageAction` default = white fill matching the page, i.e. invisible).
2. **1px hairline border, no fill** — a container that must read as a
   distinct object but must stay light: the Tool call card (`2006:15537`,
   border + radius 8, **no fill**).
3. **Faint neutral wash, no border** — the *hover* state, and static "muted
   chip" content: `MessageAction`/`QueueItem` hover fill, `TestSuiteHeader`
   hover fill (a 30%-opacity grey wash, not solid), `QueueItemFile` /
   `TaskItemFile` / status-`Badge` fill (a slightly stronger, ~solid, muted
   fill because these are static labels, not transient hover states).
4. **Filled surface + 1px hairline border** (sometimes + drop shadow) — an
   elevated card the user reads as a panel of its own: `TestResults` (fill +
   border, radius 10), `PromptInput` in every state (fill + border, radius
   10), `ModelSelectorContent` dropdown (fill + border + radius 14),
   `ContextContent` popover (fill + **shadow, no border** — the one surface
   that uses elevation instead of a border, radius 10).
5. **Filled accent** — reserved for exactly one thing: the primary action.
   `PromptInputSubmit` and the Confirmation "Approve" button are the *only*
   two controls in the whole system with a solid, non-neutral fill; every
   other control is neutral (white/none) with a border, wash, or nothing.

**Implementer's rule of thumb:** default to level 1 (nothing) or level 2
(hairline border) for anything that repeats (a chip, a row, a tool card).
Reach for level 4 (fill+border) only for the handful of things the user
should read as "a panel": the composer, the test-results block, a popover.
Reserve the accent fill (level 5) for the single primary action per screen —
never use it for a second control on the same row.

### A6. Iconography

- Icon set is **Lucide** (labelled `Icons - c/o Lucide Icons` on the Assets
  page, `2165:5292`). In this Figma file the icons are flattened to filled
  vector paths (stroke weight reads as 0 on every instance checked, e.g.
  `2088:26005`), so the original 2px-at-24px Lucide stroke cannot be
  re-measured from the file — implement with Lucide's own default (2px @
  24px viewBox), it is not a Figma-derived number.
- Sizes actually used, by frequency: **16×16 dominates** (3,944 instances) —
  this is the default icon size everywhere (message actions, tool header,
  chevrons, reasoning brain icon). **12×12** (213) is the dense/inline size —
  context-usage ring icon, file-type icons inside a chip. **14×14** (55) —
  the one composer-footer icon+label button (`Search`). **24×24** (114) is
  reference-sheet only (Assets/logo pages), never used in a live component.
- An icon sits **directly to the left of its label**, 4-8px gap, never
  centered above it — every icon+label pairing found (ToolHeader, Suggestion,
  ChainOfThoughtHeader, PromptInput toolbar buttons) is a single horizontal
  row.
- **Icon-only vs icon+label:** every row-scale action (QueueItemAction,
  MessageAction, PromptInputSubmit, the composer's mic/attach/search-toggle
  buttons at 28×28) is icon-only inside a 28px (or 24px in QueueItem) square
  hit-target. Icon+label is reserved for identity/selection controls — the
  model-selector trigger (`GPT-4o`), the search-mode toggle (`Search`), and
  suggestion pills — anything the user picks *between*, not an instantaneous
  action.

### A7. States — geometry/emphasis, not colour

| state | how it's shown | evidence |
|---|---|---|
| default | no fill, no border (level 1) | `MessageAction` default = fill matches page bg |
| hover | a faint neutral wash appears; no shape change | `MessageAction`/`QueueItem` hover fill; `TestSuiteHeader` hover = 30%-opacity wash |
| pressed | not separately modeled in this file — treat as a darker step of the hover wash | (no dedicated node found) |
| disabled | modeled elsewhere in the same design system (`Model Selector` `state=disabled`, `Open in Chat` `state=disabled`) but not sampled in this pass — treat as reduced-emphasis text/icon, same geometry |
| running / in-progress | **the control's fill never changes** — only the icon glyph swaps to a spinner (`LoaderIcon`), same 28×28 box, same accent fill | `PromptInputSubmit` `submitted+Loader` `4366:26519` — identical accent fill to `ready`/`error`, only the inner icon differs |
| success | a pill badge with a check icon + text (`Completed`/`Passed`), fill = muted wash, never accent | `Badge` `I2116:34457;2009:20859;2006:15585` |
| failure | same pill badge shape, different text/icon (`Error`, `Denied`) — **card border/fill is unchanged**, only the badge content changes | `Tool` `status=output-error` `2009:20846` vs `status=output-denied` `4346:17702` |

The load-bearing finding here: **AI Elements never recolors a container or a
primary button to signal state.** The submit button is the same blue in
`ready`, `submitted`, `streaming`, and `error` — only its icon glyph changes.
State is carried by icon swap and by badge text, never by re-tinting a
surface. This translates directly to Qt: don't repaint a button red on
error, swap its icon.

---

## B. COMPONENT ANATOMY

### B1. Conversation / Message

The container (`Conversation`, `2204:4792`) is a vertical stack, **16px
padding all sides, 16px gap between turns**. Two message shapes, both
536-544px wide in the source at 600-wide artboards — i.e. they fill the
container minus its padding.

**Assistant turn** — no bubble. Text sits directly on the panel background.

```
┌ assistant turn (536 wide, no border, no fill) ──────────────┐
│ [Sources ▸ 2 sources]                     (12/500, 16h)     │  0 gap
│ ◇ Thought for 4 seconds            ⌄      (14/400, 20h)     │  16 gap open
│   reasoning trail text…                                     │
│ This is some message content.             (14/400, 20h)     │  0 gap
│                                                               │
│ [⏮ 1 of 3 ⏭]        [👍][👎][📋][↻]        (28×28 each)     │  0 gap
└───────────────────────────────────────────────────────────────┘
```
Sub-blocks: `Sources` (536×32, collapsed) → `Reasoning` (536×36 collapsed /
536×212 open, 16px internal gap trigger→content) → `MessageContent`
(no pad, no fill, 14/400 text) → `MessageToolbar` (16px pad all sides, 16px
gap, holds `MessageBranchSelector` + `Message Actions` — four 28×28
icon-only buttons in a row, 4px gap).

**User turn** — the one message shape with a real bubble.

```
┌ user turn (536 wide) ─────────────────────────────────────┐
│                              ┌ bubble, fill+radius10 ────┐ │
│                              │ This is some message      │ │  pad 12/16
│                              │ content.        (14/400)  │ │
│                              └────────────────────────────┘ │
│ [attachments row, 96×96 each, if any]                       │
│ [⏮ 1 of 3 ⏭]        [🗑][📋][✎][↻]      (28×28 each)       │
└───────────────────────────────────────────────────────────────┘
```
`MessageContent` (`4371:14517`) = **fill + radius 10, pad 12/16/12/16** — a
level-4 filled surface (A5). This is the ONLY message-content shape that
gets a surface at all: assistant content is level-1 (nothing). States: none
per-message beyond hover-reveal of the toolbar (no dedicated hover variant
exists on `Message` itself — decide in implementation).

### B2. PromptInput (composer) + toolbar

```
┌ PromptInput — fill + 1px border, radius 10 ───────────────┐
│ [header: attach 32×32][mic 46×32 "1"][tab 73×32 "1 Tab"]  │ pad 10/8/10/6
│                                          [◔ 31.3%]         │ gap 4
├──────────────────────────────────────────────────────────┤
│ [attachment chips, 20×20 preview+remove, if any]           │ pad 10/… gap 8
├──────────────────────────────────────────────────────────┤
│  Can you explain how to use React hooks e…                 │ pad 12/12/12/12
│                                                              │
├──────────────────────────────────────────────────────────┤
│ [+][🎙][🌐 Search][GPT-4o ▾]              [➤ submit 28×28] │ pad 10/6/10/8
└──────────────────────────────────────────────────────────┘
```
Outer box radius **10**, fill + 1px hairline border (level 4). Header row
holds icon-only 32×32 hover-card triggers and the usage pill (`ContextTrigger`
74×28, radius 10, no border — matches surface-chip radius, not pill radius).
Textarea pad **12/12/12/12**. Footer (`PromptInputFooter`, pad 10/6/10/8, gap
8) holds the tool row (`PromptInputTools`, gap 4, all 28×28 icon buttons plus
one icon+label `Search` button and the model-selector trigger) on the left
and the 28×28 `PromptInputSubmit` on the right — **the only accent-filled
control in the composer** (states: `ready`/`submitted`/`streaming`/`error`
all identical accent fill, icon glyph swaps — see A7). Four component states
exist: `placeholder`, `filled`, `filled+attachments` (+36px height), `focused`
(border only gets a stronger/darker hairline, same radius/fill).

### B3. Task / TaskItem

```
Task (240 wide, no card, no border) ── pad 0/8/0/16, gap 8 between items
  • Searching "app/page.tsx, components…"        (14/400, muted)
  • Reading file          [⚛ file.tsx]           (14/400 + chip)
  • Editing file          [⚛ file.tsx][⚛ file.tsx]
```
No status icon/checkbox in the sampled `TaskItem` variants — a task row is
plain text (14/400, **muted by default**, `2005:15380`) with 0-3 trailing
`TaskItemFile` chips. Chip (`2005:15373`): fill (muted wash) + radius 8, pad
6/8/6/8, 16×16 file-type icon + `name`+`.ext` text (12pt-scale). Row gap
between text and first chip: 4-6px. Whole list: gap 8 between items, no
outer border/fill — reads purely by indentation under a `Task` heading.

### B4. Queue / QueueItem

```
┌ QueueItem (425×52) — default: no fill; hover: wash fill, radius 8 ──┐
│ ◦ Item Content                          [↻][✕]      (24×24 each)    │ pad 12/4
│   Description                                                        │
└────────────────────────────────────────────────────────────────────┘
```
Densest row in the system: pad **12/4/12/4** (not the usual 16/8), 4px
internal gap, indicator dot (10×10, pill radius), content line 14/400
20px-tall, description line 16px-tall. Two trailing `QueueItemAction`
icons, 24×24 (not 28 — the one place the system goes below its default
control size, because the row itself is only 52px tall). Row surface:
**nothing by default, a muted wash + radius 8 on hover** — exactly the A5
level-1→level-3 pattern. `QueueList` stacks items with **no explicit gap**
(rows are self-contained at 52px each). A `QueueItemFile` chip variant (used
for attachments inside the composer's queue, not the row itself) is
fill+border+radius 6, pad 4/8.

### B5. Chain of Thought / Reasoning

Two related but distinct patterns — both purely text/spacing, no card of
their own (level 1, they live inside whatever contains them):

```
ChainOfThoughtHeader:  ◇ Chain of Thought                    ⌄   (14/400)
ChainOfThoughtStep (repeatable, 368 wide, pad 16/16/16/16, gap 16):
  │ ◦  Label                              (14/500-ish "value" line)
  │ │  Description                        (12-scale muted)
  │ │  [slot: arbitrary content, 344×80]
  (│ = 1px connector line in a 16px-wide rail, ◦ = 16×16 DotIcon w/ 3×3 dot)
```
The connector is a literal `RECTANGLE` "border" (1×106) in a `line` frame,
not a border property — a genuine timeline/stepper rail. Three states:
`complete` / `pending` / `active`, same geometry, different dot/line
emphasis (no dedicated colour to report).

```
Reasoning (collapsed, 36h):  🧠 Thought for 4 seconds        ⌄
Reasoning (open, +16 gap):   [above] + reasoning-trail text (14/400, 140h)
```
`ReasoningTrigger` and `Reasoning` both carry **no fill/border of their
own** — 16px gap between trigger and content when open, 0 elsewhere.

### B6. Tool call

```
┌ Tool — 1px border, NO fill, radius 8 ──────────────────────┐
│ 🔧 database_query          [● Completed pill]          ⌄    │ pad 12 all
├──────────────────────────────────────────────────────────┤ (open only)
│ Parameters                                    (12/500 muted)│
│ [params content, 598×181]                                   │ pad 16 all,
│ [inline Confirmation row, if approval-gated]                │ gap 8
├──────────────────────────────────────────────────────────┤
│ Result                                        (12/500 muted)│ pad 16 all,
│ [result content, 598×55]                                    │ gap 8
└──────────────────────────────────────────────────────────┘
```
14 states total (`input-streaming/available`, `approval-requested/responded`,
`output-available/error/denied` × open/closed). **The card itself never
changes** across states — border, fill(none), radius 8 are constant. Only
the status pill's text+icon changes (`Completed`→`Error`→`Denied`, all same
pill shape/fill). Closed height 64px (header only), open height varies
283-458px by content. This is the strongest single example of A5's rule:
border-only container, state lives entirely in the badge.

### B7. Sources / Confirmation

```
SourcesTrigger (closed, 112×16):  ▸ 2 sources
SourcesContent (open):
  📖 Source title                                    (14/400, 16h each)
  📖 Source title
```
No card — plain rows, 16px each, `BookIcon` 16×16 + title.

```
Confirmation — inline, no card, plain text rows:
  This tool wants to access your file system. Do you…   (14/400, muted)
  Accepted: You approved this tool execution.            Accepted:(14/500)+desc(14/400 muted)
  Rejected: You rejected this tool execution.
  [ Reject ]  [ Approve ]      ← the ONLY place a secondary+primary pair sits together
```
Both buttons: radius 8, pad 10/…. **Reject** = white fill + hairline border
(level 2/4 hybrid — secondary action). **Approve** = solid accent fill, no
border (level 5 — the one other accent-filled control in the whole system
besides `PromptInputSubmit`).

### B8. Test Results

```
┌ TestResults — fill + 1px border, radius 10 (418×490) ─────┐
│ [suite summary]                          3.25s     pad 12/16, gap 8
├──────────────────────────────────────────────────────────┤
│ ▓▓▓▓▓▓▓▓▓▓▓▓░░░▒▒  12/15 passed              80%    8px track,
│ (green/red/grey stacked segments, square corners, no gap)  no radius
├──────────────────────────────────────────────────────────┤
│ ▾ Suite name                                    (14/500)    pad 16 all,
│   ✓ test one                          [passed]  (14-scale)  gap 16
│   ✓ test two                          [passed]
│   ✗ test three                        [failed]
│     └ error detail block (pad 8 all)
└──────────────────────────────────────────────────────────┘
```
Header row pad 12/16/12/16, gap 8, holds a summary + duration text
(14/400, muted). Progress bar: 8px tall, **no radius**, three contiguous
solid segments proportional to counts (no gaps between segments). Each
`Test` row: pad 16/16/16/16, gap 16, 4 states (`passed/skipped/failed/
running`) — again, only the row's leading icon+trailing badge changes, the
row itself doesn't. `TestSuiteHeader` (collapsible group title): default =
no fill; hover = a 30%-opacity grey wash (no border either state).

### B9. Model / Provider picker

```
Trigger (28-32h):  [logo] GPT-4o ▾              (12.8/500 label)
```
White fill, no border (ghost/ambient control), radius 10, pad 8 all.

```
Dropdown — fill + 1px border, radius 14 (the one 14px surface in the file)
┌────────────────────────────────────────────┐
│  Group heading                    (14pt)     │  28h row
│  [logo] Model name              ✓            │  32h row, pad implied
│  [logo] Model name                            │  32h row
│  ────────────────────────────────            │  1px separator
│  Group heading                                │
│  …                                            │
└────────────────────────────────────────────┘
```
Every model row is **32px tall**, group headings **28px**, a 1px
`CommandSeparator` between groups. Row content: 12×12 provider logo, text
(410px wide slot), optional multi-logo group (14×14 each), trailing 16×16
check indicator when selected. `state=disabled` exists as a variant
(`4369:184965` etc.) but was not sampled for geometry in this pass.

### B10. Session/Chat list — not present

No component named `Session`, `Chat list`, `Persona`, or similar exists
anywhere in the 51-page file (`figma find` on all four terms returns zero
hits in current pages). AI Elements does not model a sidebar/session-switcher
— if the panel needs one, it has no reference here; fall back to the
spacing/radius/type rules in A1-A3 rather than inventing a "Sources"- or
"QueueList"-shaped analog.

### B11. Header / context-usage element

The only "usage" surface in the system is the composer's `ContextTrigger`
pill, embedded in `PromptInputHeader` (not a standalone header bar):
`74×28, radius 10, fill matches page bg (ghost), pad 10`, holding a 16×16
two-arc "budget ring" icon (`_ContextIcon`) + a percentage string
(14/500, e.g. `31.3%`). A larger standalone `ContextContent` popover exists
(`2162:13731`, 256×170, fill+radius10+**shadow, no border** — the file's one
elevation-only surface) with header/body/footer sub-frames, footer using the
muted-wash fill role — this is what opens when the usage pill is clicked.

---

## C. WHAT TRANSLATES BADLY TO A NARROW QT DOCK (400px vs the Figma 600px)

- **Message / MessageToolbar** — the branch selector (`⏮ 1 of 3 ⏭`) plus a
  4-icon action row plus the reasoning trigger easily overflow 400px combined
  (they measure ~536px together at source scale). Collapse the branch
  selector to icon-only (`‹ 1/3 ›`, drop the "of") and cap visible actions at
  2-3, moving the rest behind an overflow `⋯`. Do **not** touch the 28×28 hit
  target or the 8px pill/8px radius — those read as "AI Elements" regardless
  of count.
- **PromptInput header row** — attach/mic/tab triggers plus the usage pill
  measure ~580px; at 400px this row wraps. Drop the tab-count and mic
  triggers to icon-only (they already are, just narrower gaps), and let the
  usage pill be the first thing to move to a second row or into a tooltip —
  it's the least time-critical item on that row.
- **PromptInput footer toolbar** — `[+][mic][Search label][Model label][➤]`
  is ~230px of controls plus whatever textarea width remains; at 400px the
  `Search` and model-name labels are the first to go icon-only (drop the
  text, keep the icon, keep the 28px height and 8px radius). The submit
  button must stay last, right-aligned, unchanged.
- **QueueItem** — its two trailing 24×24 action icons already survive at
  400px (QueueItem itself was designed at 425px, close to the target width);
  what breaks is the description line wrapping under the icons instead of
  staying single-line — accept the wrap, don't shrink the 12/4 padding or
  the 8px radius to fight it.
- **Tool call** — parameters/result content (`598px` wide slot) is the one
  place raw JSON/code needs a hard width cap or horizontal scroll at 400px;
  the card chrome itself (border, radius 8, 16px pad) is unaffected and must
  not change.
- **Test Results** — the 418px-wide card is already dock-width; the risk is
  the summary row (`suite name + 3.25s duration`) wrapping. Truncate the
  suite name with an ellipsis before shrinking the 12/16 header padding.
- **ChainOfThoughtStep "slot" content** (arbitrary 344×80 block) — this is
  the one place free-form content (a diff, a snippet) is expected to be
  wide; it will need its own internal scroll/wrap at 400px. The 16px-wide
  connector rail and 16/16 card padding must stay fixed-width regardless.
- **Model selector dropdown** — 512px popover with 410px text slots for
  model names; at 400px this must become a narrower list (drop the
  multi-logo group preview, keep the single provider logo) rather than
  shrinking the 32px row height, which is what makes it read as a
  "Command"-style list.
- **Conversation container** — 16px gutter on both sides already assumes a
  600px canvas gives ~568px of content; at 400px that leaves ~368px for
  message content, which is workable — do not shrink the 16px gutter or the
  panel stops reading as having any margin at all (it becomes edge-to-edge,
  which no AI Elements surface does).
- **Suggestion pills / Attachment chips** — both are pill/rounded-rect rows
  that wrap naturally; the only risk is cramming several on one line. Let
  them wrap to a second row (they already support `HUG` sizing) rather than
  truncating their label text.

---

## D. THE TEN THINGS THAT MAKE IT LOOK LIKE AI ELEMENTS

Ranked; each is one Qt/QSS-implementable rule.

1. **Never recolor a control for state.** Same fill for ready/running/error;
   only the icon glyph changes (spinner, alert, check). This is the single
   most distinctive trait — most chat UIs go red/green, this one doesn't.
2. **Exactly one accent-filled control per screen area.** Submit button and
   "Approve" are solid `palette(Highlight)`; every other control is neutral
   (border, wash, or nothing). Two accent buttons on one row is off-model.
3. **8px radius on every control** (button, chip, tool card); **10px radius
   on every surface/card** (message bubble, composer, results panel,
   popover trigger); **full pill only for status labels**, never for a
   container.
4. **Assistant text has no bubble.** Only the user's turn gets a filled
   surface; assistant content sits flush against the panel background,
   14px/400 weight, no padding of its own.
5. **1px hairline border with no fill is the default container**, not a
   filled card — reach for a filled surface only for the handful of things
   meant to read as "a distinct panel" (composer, results, popovers).
6. **Hover = a faint neutral wash, nothing else** — no border appears, no
   shadow, no radius change, just a low-opacity grey fill under the same
   geometry.
7. **28×28 is the default control size** for every icon-only action; drop
   to 24×24 only inside the densest list row (QueueItem), never elsewhere.
8. **16px icons next to text, 12px icons in dense/inline contexts** (usage
   pill, file-type glyphs) — never 24px icons in a live component.
9. **8px is the default gap**, 16px is reserved for "space between
   sibling blocks" (card padding, gutter, gap between conversation turns) —
   don't use 16px inside a single row/chip.
10. **A heading is never a bigger font** — it's the same 14px body size at
    500 weight instead of 400. Nothing in-panel goes above 14/500.
