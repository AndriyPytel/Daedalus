# FreeCAD 1.0 Technical Verbs — closed-set analysis

## 1. Is Execute constrained to a registry of tools, or does it write arbitrary Python?

**It writes arbitrary Python. There is no per-operation Tool.** A verb list is
therefore a naming convention carried by the prompts, not a set enforced by
the code that calls FreeCAD. It is enforceable only if a lint rule is added
to check the `TypeId` string against an allow-list.

Evidence:

- `core/tools.py:35-45` (`Tool`) and `:37-83` (`ToolRegistry`) define a
  registry of named, schema-checked functions — this part IS closed, but it
  is a registry of **capabilities** (`snapshot`, `describe_object`,
  `apply_step`, `check_assertions`, ...), not of **modelling operations**.
- `core/freecad_tools.py:906-931` (`_register_apply` / `apply_step`) is the
  one Tool that touches geometry. Its `code` argument is
  `"Python for this Step. doc, App, Part, Sketcher and PartDesign are in
  scope."` (`core/freecad_tools.py:915-921`) — free-form Python, not a
  parameterised operation call.
- `core/preflight_worker.py:318` — `STEP_MODULES = ("Part", "Sketcher",
  "PartDesign", "Materials")` — sets what is injected into the exec
  namespace (`core/preflight_worker.py:321-347`, mirrored in
  `core/apply.py:178`). This is the actual code-level ceiling: any FreeCAD
  object type these four modules can build is reachable by
  `doc.addObject("Whatever::Type", ...)`; anything needing a fifth module
  (Draft, Surface, Assembly, TechDraw, Mesh, Sheet Metal) is only reachable
  if the Step's own code adds `import <module>` — nothing in
  `core/lint.py` blocks that.
- `core/lint.py:76` — `GUI_NAMES = ("Gui", "FreeCADGui")` — the only import
  names `lint()` rejects (`core/lint.py:290-300`). Sketcher constraint
  spelling, PartDesign Body membership, and the traps in
  `core/freecad_tools.py:17-72` are checked for correctness, never for
  which operation was chosen.
- `core/preflight.py` runs the Step against a real `FreeCADCmd` and reports
  the traceback; it validates that code *works*, not that it used an
  *approved* operation.

Conclusion: a closed verb list can be **documented** and **taught** to
Design/Plan/Execute, but nothing today stops Execute from writing
`doc.addObject('Part::Extrusion', ...)` when the Plan said "pad", or from
importing Draft and building a wall. Enforcement, if wanted, is a new
`core/lint.py` check on `addObject`'s first argument against a fixed
`TypeId` allow-list.

## 2. Existing closed vocabulary already in the repo

`config/prompts/design.md:566-574` already defines a closed naming
vocabulary, but it is for feature **identifiers** in the Design JSON, not for
the FreeCAD API call Execute makes:

- kind: `hole, bore, slot, pocket, cutout, pad, fillet, chamfer, boss, rib,
  thread, counterbore, countersink, groove, notch, tab, lug, ear, flange,
  step, shoulder`
- place: `left, right, front, rear, top, bottom, upper, lower, centre, inner,
  outer`
- datum names and envelope keys, also closed lists.

These are drawing-vocabulary nouns (what a feature *is*), several-to-one
against the Technical Verbs below (what FreeCAD *builds*) — e.g. `boss`,
`lug`, `ear`, `tab`, `rib` are all built with the verb `pad`; `counterbore`
and `countersink` are both built with the verb `hole`.

## 3. Technical Verb table

One approved verb, the object type it creates, the synonyms Design/Plan/
Execute must never use in its place. Source for every FreeCAD type and its
toolbar grouping: `wiki.freecad.org/PartDesign_Workbench` and
`wiki.freecad.org/Part_Workbench` (fetched over HTTP, FreeCAD 1.0/1.1 wiki
content, checked 2026 against the "Tools" / toolbar sections).

### Additive

| verb | creates | bans |
|---|---|---|
| `pad` | `PartDesign::Pad` | extrude, protrude, raise, boss-out |
| `revolve` | `PartDesign::Revolution` | spin, lathe, turn |
| `additive-loft` | `PartDesign::AdditiveLoft` | blend, transition, morph |
| `additive-sweep` | `PartDesign::AdditivePipe` | pipe, extrude-along-path |
| `additive-helix` | `PartDesign::AdditiveHelix` | coil, spiral-extrude, thread-pad |
| `additive-primitive` | `PartDesign::Additive{Box,Cylinder,Sphere,Cone,Ellipsoid,Torus,Prism,Wedge}` | insert-shape, stamp |

### Subtractive

| verb | creates | bans |
|---|---|---|
| `pocket` | `PartDesign::Pocket` | cut, carve, remove, hollow-out, mill, recess |
| `hole` | `PartDesign::Hole` | drill, bore (as verb), tap, ream |
| `groove` | `PartDesign::Groove` | revolve-cut, spin-cut, turn-cut |
| `subtractive-loft` | `PartDesign::SubtractiveLoft` | blend-cut, transition-cut |
| `subtractive-sweep` | `PartDesign::SubtractivePipe` | pipe-cut, sweep-remove |
| `subtractive-helix` | `PartDesign::SubtractiveHelix` | coil-cut, thread-cut |
| `subtractive-primitive` | `PartDesign::Subtractive{Box,Cylinder,Sphere,Cone,Ellipsoid,Torus,Prism,Wedge}` | remove-shape, stamp-out |

### Dress-up

| verb | creates | bans |
|---|---|---|
| `fillet` | `PartDesign::Fillet` | round (as verb), blend-edge, smooth |
| `chamfer` | `PartDesign::Chamfer` | bevel, angle-cut, break-edge |
| `draft` | `PartDesign::Draft` | taper, angle-face |
| `thickness` | `PartDesign::Thickness` | shell, hollow, wall-out |

### Transformation

| verb | creates | bans |
|---|---|---|
| `mirror` | `PartDesign::Mirrored` | reflect, flip-copy |
| `linear-pattern` | `PartDesign::LinearPattern` | array, repeat, copy-along |
| `polar-pattern` | `PartDesign::PolarPattern` | circular-array, rotate-copy |
| `multi-transform` | `PartDesign::MultiTransform` | combo-pattern, stacked-transform |
| `scale` | `PartDesign::Scaled` | resize, blow-up |

### Datum / scaffolding

| verb | creates | bans |
|---|---|---|
| `sketch` | `Sketcher::SketchObject` | profile, drawing, outline-object |
| `datum-plane` | `PartDesign::Plane` | work-plane, reference-plane |
| `datum-line` | `PartDesign::Line` | axis (as object), construction-line |
| `datum-point` | `PartDesign::Point` | reference-point, marker |
| `local-coordinate-system` | `PartDesign::CoordinateSystem` | LCS (as verb), origin-set |
| `sub-shape-binder` | `PartDesign::SubShapeBinder` | link-geometry, external-ref |
| `body` | `PartDesign::Body` | container, part-holder |

### Boolean (fallback, outside a Body — `Part::Feature`, per `execute.md`: "If the shape genuinely belongs outside a Body, build it as a Part::Feature")

| verb | creates | bans |
|---|---|---|
| `fuse` | `Part::MultiFuse` / `Part::Fuse` | union, join, add-solids |
| `cut` | `Part::Cut` | subtract, difference, remove-solid |
| `common` | `Part::Common` | intersect, intersection |

## 4. Gaps — FreeCAD 1.0 operations this repo's code cannot currently reach

Given `STEP_MODULES = ("Part", "Sketcher", "PartDesign", "Materials")`
(`core/preflight_worker.py:318`) and that Design/Plan/Execute never name
anything outside PartDesign+Part+Sketcher (`config/prompts/design.md`,
`config/prompts/plan.md`, `config/prompts/execute.md` — grepped for every
verb in the table above, no hits for the items below):

- **Draft workbench** (2D drafting/BIM: wall, window, `Draft::Wire` offsets
  usable as sketch substitutes) — not in `STEP_MODULES`, never named.
- **Surface workbench** (`Surface::Filling`, `Surface::Sections`, healing) —
  same: not injected, not named. A drawing with a genuinely free-form
  surface (not loft/sweep/revolve) cannot be expressed.
- **Assembly / joints** — out of scope by design: the pipeline models one
  part (`config/prompts/design.md` speaks throughout of "the part"), so
  multi-body assembly constraints are structurally unreachable, not merely
  unlisted.
- **TechDraw** (2D drawing output) — different domain (input side), not a
  modelling verb; correctly absent.
- **Sheet Metal Workbench** — external addon, not part of stock FreeCAD 1.0;
  correctly absent.
- **PartDesign "Additional tools"** — `Involute Gear`, `Sprocket`, `Shaft
  Design Wizard` (`wiki.freecad.org/PartDesign_Workbench`, "Additional
  tools" section) are GUI task-panel macros, not a documented
  `doc.addObject(TypeId, ...)` call; unverified whether Execute's exec
  namespace can drive them headlessly. Flagged as a risk, not confirmed
  unreachable.
- **Part-level `Offset` (3D/2D), `Loft`, `Sweep`, `RuledSurface`,
  `Section`/`Cross-Sections`** — technically reachable (`Part` module is
  injected) but never named as an approved verb; a drawing whose only route
  is a Part-level (non-PartDesign) loft/sweep/offset instead of the
  PartDesign additive/subtractive equivalents would currently be built by
  an unapproved route unless the verb list is extended to cover them.
- **Sketcher External Geometry** (`Sketcher::External`, referencing another
  Body's edge) — `Sketcher` module is injected so the call would run, but no
  verb in Design/Plan vocabulary names it; a part whose datum is another
  Body's edge has no expressible Step today.

None of these gaps is blocked by `core/lint.py` or `core/preflight.py`
today — they are gaps in the **named** vocabulary, not in what
`apply_step` could technically execute. Closing them (if this drawing
domain needs them) means adding both the module to `STEP_MODULES` (where
missing) and the verb to this table and to `design.md`'s closed
vocabulary.

## 5. Count

**32 approved verbs** in the core table above:
6 additive + 7 subtractive + 4 dress-up + 5 transformation + 7
datum/scaffolding + 3 boolean = 32.

32 is under 40 — **one core list is practical**, no split into
core/extended needed. If the Part-level fallbacks in the gaps section
(`offset`, `part-loft`, `part-sweep`) are added to cover drawings this
pipeline currently cannot build, the count rises to 35 and stays under 40.
