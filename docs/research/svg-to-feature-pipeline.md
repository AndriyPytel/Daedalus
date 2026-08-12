# From vector paths to a description a CAD-building agent can use

*Research note. Written against primary sources and against measurements taken
on this repo's own test sheets (`tests/modelmania/*.pdf`). Nothing outside
`docs/research/` was touched.*

## Question

Given vector geometry of a drawing (SVG, DXF, or the path items inside a PDF),
how do you get from *paths* to a *textual description* a CAD-building agent can
act on — "this contour is a Ø35 circle at (20, 30)", "this is a rectangle with
4 corner radii R2"? And is the pipeline
`PNG → SVG → primitives → compact textual scene → LLM` sound for FreeCadCopilot?

## Executive summary

1. **On this repo's actual sheets there is no vector part geometry to recover.**
   Every view body in all four `tests/modelmania/*.pdf` is an **embedded raster
   at 186 DPI**; only the annotation (dimension lines, extension lines, leaders,
   section arrows, hatching) is vector. Measured with PyMuPDF
   `page.get_image_info()` / `page.get_drawings()`. `sheet_reader._drawn()`
   already half-knows this (`from_a_picture`).
2. **`page.get_drawings()` does not remove the need for tracing here, but it is
   still the highest-value data on the sheet** — it is the *dimension
   apparatus*, exactly, in exact coordinates.
3. **PDF has no arc or circle operator.** PyMuPDF item types are
   `("l", p1, p2)`, `("c", p1..p4)` cubic Bézier, `("re", rect, orientation)`,
   `("qu", quad)` — nothing else
   ([PyMuPDF `Page.get_drawings`](https://pymupdf.readthedocs.io/en/latest/page.html)).
   On these sheets the item census is **100 % `"l"`** (585 / 1177 / 2851 / 896 /
   3450 / 3502 segments per page, zero `c`, zero `re`): SolidWorks flattens even
   the arcs. So "recover a circle" is always a *fitting* problem, never a
   *parsing* problem, for PDF input.
4. **DXF is the only one of the three formats that carries CAD semantics**:
   `CIRCLE`, `ARC`, and above all `DIMENSION` with its definition points
   (`defpoint2` = start of first extension line, `defpoint3` = start of the
   second, `dxf.text_midpoint`, `dimtype` 0–6 = linear/aligned/angular/diameter/
   radius/ordinate)
   ([ezdxf `Dimension`](https://ezdxf.readthedocs.io/en/stable/dxfentities/dimension.html)).
   If the user can ever be persuaded to export DXF, the whole "which number
   measures which edge" problem disappears — it is a field lookup. SVG and PDF
   have no such entity.
5. **A raster trace is accurate enough to *check* a number, not to *read* one.**
   Measured here: on the 186 DPI embedded view of `mm25-p1-1.pdf`, an algebraic
   circle fit to the traced hole contour returns **Ø35.00 mm** (true value 35),
   and the part outline returns **150.7 × 100.8 mm** (true 150 × 100). Different
   fitting choices spread over ≈ 34.4–35.5 mm, i.e. **±1.5 %**. That will never
   produce "35" on its own, but it refutes "30" and it refutes "100 where the
   sheet says 108".
6. **The single highest-leverage result of this note is a scale/association
   consensus that reproduces the ADR-0019 failure and catches it.** Merging
   collinear PDF segments into dimension-line spans and voting on
   `mm_per_point = printed_value / span` recovers the plot scale independently
   on every test page (0.3501 mm/pt ≈ 1:1 on the 2023/2024 sheets, 0.7002 mm/pt
   ≈ 1:2 on the 2025 sheets), and on the 2023 sheet it pairs the printed word
   `108` with a **306.1 pt span = 107.18 mm**, residual −0.82 mm. A reading of
   `100` is 7.2 mm — twenty residuals — away from the ink. Full table below.
7. **`PNG → SVG → primitives` works and installs cleanly on macOS**: `vtracer`
   (MIT, arm64 wheels, 0.03 s per view) traces the shaded view; but it emits
   **only cubic Béziers — 550 `C`, zero `L`, zero `A`** for one view. Every
   tracer does this. The primitive fitting is *your* job, not the tracer's.
8. **Do not route the trace through FreeCAD's own importers.** Verified locally
   on FreeCAD 1.1.3: `importSVG.insert()` on the traced view yields 36 objects /
   550 edges, of which **523 `BezierCurve` + 27 `BSplineCurve` and 0 circles,
   0 lines**; `Draft.make_sketch(..., autoconstraints=True)` then produces a
   sketch with 550 geometries, 616 constraints and **5206 degrees of freedom**.
   That is not a model, it is confetti.
9. **Reject "trace the sheet, build from the trace".** Accept **"read the
   numbers from the text layer, and use geometry — vector spans on a vector
   sheet, traced contours on a raster one — as an independent witness that
   accepts or rejects each read value and binds it to a feature."** The text
   layer cannot misread; the geometry cannot lie about scale; neither alone is
   enough.
10. **Minimum viable version needs zero new dependencies** for vector sheets
    (PyMuPDF is already in the repo) and **one** for raster sheets (`vtracer`,
    MIT, or `opencv-python-headless`, Apache-2.0). Both install via pip on macOS
    arm64; both were installed and exercised for this note.

---

## Findings

### 1. What the repo's sheets actually contain (measured, not read)

Run against `tests/modelmania/` with the repo's own PyMuPDF 1.28.2:

| file | page | vector path items | image count | images DPI | text words |
|---|---|---|---|---|---|
| `mm25-p1-1.pdf` | 0 | 585 (all `"l"`) | 5 | 186 | 89 |
| `model-mania_-in-person-2023.pdf` | 0 | 1177 (all `"l"`) | 6 | 186 | 121 |
| `model-mania_-in-person-2023.pdf` | 1 | 2851 (all `"l"`) | 6 | 186 | 109 |
| `model-mania-2024.pdf` | 0 | 896 (all `"l"`) | 7 | 186 | 101 |
| `model-mania-2024.pdf` | 1 | 3450 (all `"l"`) | 8 | 186 | 158 |
| `mm25-p2.pdf` | 0 | 3502 (all `"l"`) | 6 | 186 | 140 |

Every image bbox divides out to 186 DPI exactly. The images are *shaded colour
renders* (yellow faces, red fillet faces, black edges), not line art — which
turns out to help, because a colour-region tracer segments the part for free.

Consequences, in order of importance:

- **Re-rendering the PDF at a higher DPI cannot recover geometry.** The page
  renders at any DPI you like, but the view content is a fixed 186 DPI bitmap
  upscaled. `sheet_reader.LEGIBLE_PX` still buys real resolution for the *text*
  (which is vector) and for the *dimension apparatus* (also vector); it buys
  nothing for the part outline. This deserves to be written down in
  `sheet_reader`'s docstring, which currently argues DPI as if it were free.
- **`_drawn()` is reporting annotation, not part edges,** on every one of these
  sheets. That is not a bug — an extension line is drawn *from the face it
  measures*, which is why the recorded 2023 run could have got the arm position
  right — but the entity list should be labelled as such.
- `path["dashes"]` is populated and discriminating: on the 2023 sheet, 928
  segments are solid `[] 0`, 165 carry `[ 18 3.6 3.6 3.6 3.6 3.6 ] 11.895`
  (long-dash-dot = **centre line**, ISO 128 type G). A centre line is an axis of
  revolution or a symmetry plane; that is free semantics the reader currently
  throws away.
- `path["color"]` is also discriminating but *not* semantic: on `mm25-p1-1` the
  segments split (0,0,1) blue ×329, (0.5,0,0.25) ×179, (1,0,0) red ×36,
  (0,0.5,0) green ×26, (0.5,0,0.5) ×15. Green = dimension apparatus on that
  sheet, blue = section/hatch, but this is a SolidWorks style convention, not a
  standard. Use it as a hint that clusters entities, never as a class label.

### 2. SVG: what the spec gives you and what it does not

- SVG path data commands are `moveto`, `lineto`, cubic and quadratic `curveto`,
  `arc`, `closepath` — and *elliptical* arcs only, in **endpoint
  parameterisation**: `A rx ry x-axis-rotation large-arc-flag sweep-flag x y`.
  The centre is *computed*, not stored
  ([SVG 2 §9.3.8](https://www.w3.org/TR/SVG2/paths.html)).
- To get `(cx, cy, r, θ1, Δθ)` — the form a CAD agent wants — you must run the
  normative endpoint→centre conversion, including the out-of-range-radii
  correction (`Λ > 1 ⇒ scale rx, ry by √Λ`)
  ([SVG 1.1 Appendix F.6.5 / F.6.6](https://www.w3.org/TR/SVG11/implnote.html)).
  This is ~30 lines and both `svgelements` and `svgpathtools` already implement
  it.
- SVG has `<circle>` and `<ellipse>` elements, so a *hand-authored or
  CAD-exported* SVG may carry a real circle. **No tracer ever emits one.**
  Verified: `vtracer` output for one view is 36 `<path>` elements, command
  census `M 41, C 550, L 0, Q 0, A 0, Z 41`.
- SVG has **no dimension entity, no arrowhead semantics, no layers with
  meaning, no units tied to the part**. A traced SVG is a picture of the
  drawing, at pixel scale, and nothing more.
- `page.get_svg_image()` in PyMuPDF converts a PDF page to SVG
  ([Page docs](https://pymupdf.readthedocs.io/en/latest/page.html)) — but for a
  vector sheet this is strictly *lossier* than `get_drawings()` for our purpose,
  because it discards the dash pattern/colour grouping into presentation
  attributes you then have to re-parse. Don't detour through SVG on the vector
  path.

### 3. DXF/DWG: the only format that carries the answer

- **DXF is an entity model, not a picture.** `CIRCLE` stores `dxf.center` and
  `dxf.radius` directly
  ([ezdxf `Circle`](https://ezdxf.readthedocs.io/en/stable/dxfentities/circle.html));
  `ARC` adds start/end angle; `LWPOLYLINE` carries per-vertex *bulge* values
  that encode tangent arcs exactly.
- **`DIMENSION` is the thing SVG and PDF cannot express.** One entity carries
  `dimtype` (0 linear/rotated, 1 aligned, 2 angular, 3 diameter, 4 radius,
  5 angular-3P, 6 ordinate), `defpoint` (dimension-line location), `defpoint2`
  and `defpoint3` (the two extension-line origins — i.e. *the two features
  being measured, in model coordinates*), `defpoint4` (diameter/radius),
  `text_midpoint`, `dimstyle`, plus an anonymous BLOCK holding the drawn
  representation
  ([ezdxf `Dimension`](https://ezdxf.readthedocs.io/en/stable/dxfentities/dimension.html),
  which cites the Autodesk DXF Reference).
  So on DXF, "which number measures which edge" is `dim.dxf.defpoint2` and
  `dim.dxf.defpoint3` — a lookup, with no inference and no vision.
- Caveat from the same source: DIMENSION is *reused* for dimensional
  constraints (no `dimtype`, layer `*ADSK_CONSTRAINTS`); check
  `Dimension.is_dimensional_constraint` before trusting an entity.
- **ezdxf**: MIT licence, pure Python + optional C extensions, wheels for macOS,
  supports R12 → R2018 read/write
  ([ezdxf quick-info](https://ezdxf.readthedocs.io/en/stable/)).
  It reads **DXF only, not DWG**. DWG is Autodesk's binary format; the only
  public description is the
  [Open Design Alliance "Open Design Specification for .dwg files"](https://www.opendesign.com/files/guestdownloads/OpenDesign_Specification_for_.dwg_files.pdf)
  (fetched, 2.3 MB PDF, freely downloadable), and implementing it is a project,
  not a dependency. Practical route for DWG: ask the user for DXF, or shell out
  to the ODA File Converter (free-as-in-beer, closed source, separate install).
- ezdxf also warns that ACIS-based solid entities (3DSOLID/REGION/SURFACE) are
  proprietary SAT/SAB blobs with no open library
  ([FAQ](https://ezdxf.readthedocs.io/en/stable/faq.html)) — relevant only if
  someone hands us a DXF containing 3D bodies.

### 4. Libraries, judged for this pipeline

All of the following were installed into a scratch venv on macOS 26.6 arm64
with `uv pip install` and imported successfully (Python 3.11):

| library | licence | role here | verdict |
|---|---|---|---|
| **PyMuPDF** 1.28.2 | AGPL-3.0 (already a repo dependency) | `get_drawings`, `get_text("words")`, `get_image_info(xrefs=True)` + `extract_image` | **already in; do more with it** |
| **vtracer** 0.6.15 | MIT ([PyPI](https://pypi.org/project/vtracer/)) | raster → SVG colour-region trace, 0.03 s/view, macOS arm64 wheel | **the one new dependency worth taking** |
| **opencv-python-headless** 5.0 | Apache-2.0 ([PyPI](https://pypi.org/project/opencv-python/)) | `findContours`, `approxPolyDP`, `minEnclosingCircle`, `fitEllipse`, `HoughCircles` | good alternative to vtracer; heavier wheel |
| **scikit-image** 0.26 | BSD-3 | `measure.find_contours`, `approximate_polygon` (= RDP), `CircleModel`/`EllipseModel` + `ransac` ([API](https://scikit-image.org/docs/stable/api/skimage.measure.html)) | pure-Python fitting toolkit; pulls scipy |
| **svgelements** 1.9.6 | MIT ([PyPI](https://pypi.org/project/svgelements/)) | SVG parsing incl. arc endpoint↔centre, transforms, units | best if you must consume third-party SVG |
| **svgpathtools** 1.7.2 | MIT | path sampling, `Arc` objects, bbox/intersections | used here to sample traced paths |
| **shapely** | BSD-3, GEOS is LGPL-2.1 ([PyPI](https://pypi.org/project/shapely/)) | closed-contour boolean ops, containment ("this circle is inside that face") | only if you need boolean topology |
| **pyclipper** | MIT, Clipper core Boost licence ([PyPI](https://pypi.org/project/pyclipper/)) | integer-robust polygon clipping/offsetting | not needed unless you offset |
| **potrace** | **GPL-2** ([potrace.sourceforge.net](https://potrace.sourceforge.net/)) | classic bitmap tracer, has a DXF backend | **reject on licence**: FreeCAD is LGPL; a GPL trace library infects the workbench. `pypotrace` on PyPI is likewise GPL |

### 5. FreeCAD's own machinery — what it will and will not do

Verified by running FreeCAD 1.1.3 (`/Applications/FreeCAD.app`) in console mode
against the `vtracer` output of one view:

```
objects imported: 36
edge curve types: {'BSplineCurve': 27, 'BezierCurve': 523}   total 550
Draft.make_sketch(..., autoconstraints=True)
    -> geometry 550, constraints 616, DoF 5206
```

- `importSVG` ([source, LGPL-2.1-or-later](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Draft/importSVG.py))
  *does* map `<circle>`/`<ellipse>` elements to `Part.makeCircle` /
  `Part.Ellipse`, and its `approximateWire(wire, tolerance)` keeps
  line/arc edges and approximates the rest. But traced SVG has no `<circle>`
  elements, so none of that fires; and `importSVG` applies a 96 DPI
  px→mm matrix (0.264583), so imported coordinates are millimetres *of paper*,
  never millimetres of part.
- `Part.BSplineCurve.toBiArcs(tolerance)` exists and returns "a list of arcs and
  lines"
  ([BSplineCurve.pyi](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Part/App/BSplineCurve.pyi),
  implemented in `BSplineCurveBiArcs.cpp`). Tested per-edge on the traced hole:
  each tiny traced Bézier degrades to **a single `LineSegment`**. Biarc fitting
  is per-curve; the traced circle is 11–32 separate curves, so it never sees a
  circle. **Fit globally over the sampled contour, not per edge.**
- **Sketcher's Python API is the right target once you have primitives**:
  `SketchObject.addGeometry(Part.Circle/LineSegment/ArcOfCircle, isConstruction)`,
  `addConstraint(Sketcher.Constraint(...))`, `solve()`, `DoF`,
  `ConflictingConstraints`, `RedundantConstraints`, and a whole analysis suite —
  `autoconstraint()`, `detectMissingPointOnPointConstraints()`,
  `detectMissingVerticalHorizontalConstraints()`,
  `detectMissingEqualityConstraints()`, `makeMissing*()`
  ([SketchObject.pyi](https://raw.githubusercontent.com/FreeCAD/FreeCAD/main/src/Mod/Sketcher/App/SketchObject.pyi)).
  `DoF == 0` after adding the *read* dimensions is a hard, cheap correctness
  signal: a sheet whose numbers we read correctly should fully constrain its
  sketch; leftover DoF or a conflict is a reading error, reported not resolved
  — which is exactly `design.py`'s existing contract.
- **The Image workbench no longer exists.** `src/Mod` in FreeCAD `main` has no
  `Image` directory, and the installed FreeCAD 1.1.3 `Contents/Resources/Mod`
  has none either. Any plan that says "drop the drawing in as an ImagePlane and
  trace over it" is planning against a workbench that was removed; and manual
  tracing is a *human* workflow anyway, not something an agent can drive.

### 6. Algorithms, and which ones actually matter here

- **Polyline simplification — Ramer–Douglas–Peucker.** Ramer, *An iterative
  procedure for the polygonal approximation of plane curves*, CGIP 1(3), 1972,
  [doi:10.1016/S0146-664X(72)80017-0](https://doi.org/10.1016/S0146-664X(72)80017-0);
  Douglas & Peucker, *Algorithms for the reduction of the number of points…*,
  Cartographica 10(2), 1973,
  [doi:10.3138/FM57-6770-U75U-7727](https://doi.org/10.3138/FM57-6770-U75U-7727).
  Available as `skimage.measure.approximate_polygon` and `cv2.approxPolyDP`.
  Use it to find **corners** (the vertices RDP keeps at a tolerance ≈ one
  stroke width are the real corners), not to produce final geometry.
- **Circle fitting.** The algebraic (Kåsa) fit is a 3×3 least squares and is what
  was used for every number in this note; it is biased for short arcs, which
  Pratt (*Direct least-squares fitting of algebraic surfaces*, SIGGRAPH 1987,
  [doi:10.1145/37401.37420](https://doi.org/10.1145/37401.37420)) and Taubin
  (*Estimation of planar curves, surfaces and nonplanar space curves…*,
  IEEE TPAMI 13(11), 1991, [doi:10.1109/34.103273](https://doi.org/10.1109/34.103273))
  correct. For a full traced circle the difference is below the pixel noise
  here; for a 60° fillet arc it is not, and Taubin is the one to use.
  `skimage.measure.CircleModel` is a total-least-squares circle estimator.
- **RANSAC** (Fischler & Bolles, CACM 24(6), 1981,
  [doi:10.1145/358669.358692](https://doi.org/10.1145/358669.358692)),
  via `skimage.measure.ransac`, is the right frame for *both* the geometric
  fits and — the useful insight — for the **scale estimate** (§7): the correct
  `mm_per_point` is the one with the largest consensus set among
  (printed number, measured span) pairs.
- **Tangent continuity / symmetry** are the tests that turn primitives into
  *features*: two arcs of equal radius mirrored about a dashed centre line are
  a hole pattern; a corner where two lines meet through an arc whose tangents
  match both is a fillet, not a chamfer, and its radius is the R-callout's
  candidate. These are cheap predicates over already-fitted primitives, and
  they are what makes the textual scene description say "4-corner-radiused
  rectangle" rather than "8 curves".
- **Machine-learning prior art, for calibration of expectations**: parametric
  primitives can be inferred from sketch *images* end-to-end
  (PICASSO, [arXiv:2407.13394](https://arxiv.org/abs/2407.13394); Vitruvion,
  [arXiv:2109.14124](https://arxiv.org/abs/2109.14124), trained on SketchGraphs,
  [arXiv:2007.08506](https://arxiv.org/abs/2007.08506)); engineering-drawing
  vectorisation and component segmentation is an active problem
  ([arXiv:2212.00290](https://arxiv.org/abs/2212.00290)); and the current
  state of the art for *reading annotations* is fine-tuned VLMs plus an
  oriented-bbox detector, not classical CV
  ([arXiv:2411.03707](https://arxiv.org/abs/2411.03707),
  [arXiv:2505.01530](https://arxiv.org/abs/2505.01530),
  [arXiv:2506.17374](https://arxiv.org/abs/2506.17374)); binding drawing
  annotations to 3D CAD features with LLM reasoning is itself a published open
  problem ([arXiv:2602.18296](https://arxiv.org/abs/2602.18296)).
  None of these ship as a pip-installable, offline, macOS-friendly component
  today. They confirm the shape of the problem; they are not a dependency.

### 7. The measurement that matters: geometry as a witness against a misread number

Two experiments, both run here, both reproducible with what is already in the
repo.

**(a) Vector sheet — dimension-span consensus.** Collect every `"l"` item on the
page; merge segments that are collinear (same angle bucket, same perpendicular
offset) and within 60 pt of each other into one **span** — this is what
reassembles a SolidWorks dimension line that has been broken around its own
text. Then for every numeric word within 12 pt of a span, vote for
`k = value / span_length`. The winning `k` is the sheet's true plot scale:

| file | page | winning `k` (mm/pt) | implied scale | numbers explained |
|---|---|---|---|---|
| `mm25-p1-1.pdf` | 0 | 0.7002 | 1:1.98 | 50, 50, 100, 150 |
| `mm25-p2.pdf` | 0 | 0.7002 | 1:1.98 | 40, 50, 100, 150 |
| `model-mania_-in-person-2023.pdf` | 0 | 0.3501 | 1:0.99 | 14, 15, 25, 30, 30, 42.5, 65.5, **108** |
| `model-mania_-in-person-2023.pdf` | 1 | 0.3501 | 1:0.99 | 25, 30, 73 |
| `model-mania-2024.pdf` | 0 | 0.3501 | 1:0.99 | 15, 20, 30, 40, 60, 80 |
| `model-mania-2024.pdf` | 1 | 0.3501 | 1:0.99 | 15, 15, 20, 30, 30, 40, 60, 75 |

Per-word detail on the 2023 sheet at `k = 0.35014`:

```
word    span(pt)   implied mm   residual
  30       85.1        29.80      -0.20
  14       39.7        13.90      -0.10
  15       42.5        14.88      -0.12
 108      306.1       107.18      -0.82     <-- ADR-0019's number
42.5      120.4        42.16      -0.34
65.5      185.7        65.02      -0.48
  25       70.8        24.79      -0.21
```

Residuals sit at −0.1 … −0.8 mm (a small systematic under-read: the merged span
is the dimension line between arrow tips, not the extension-line feet). **A
reading of "100" against a 107.18 mm span is 7.2 mm out — an order of magnitude
outside the residual band.** This is the ADR-0019 failure, caught arithmetically,
on the sheet where it happened, with no model in the loop.

Two honest caveats. First, coverage is partial: 8 of 29 numeric words on that
page get a span (radii, thicknesses annotated by leader, and title-block numbers
do not — correctly, they are not linear dimensions). Second, the naive version
produces false pairs (a stray `6` matched a 123.5 pt span; the year `2023`
matched a 306.1 pt span). Both are handled by the same rule: **accept a pairing
only when the residual is inside the band, otherwise report it as unmatched** —
never invent a value from the span.

Note also that the title-block `SCALE:` string is *not* a safe substitute for
this consensus. `sheet_reader._millimetres_per_point()` currently trusts it; the
consensus is measured off the sheet's own ink and agrees with the title block on
all six pages, so it can be used to *verify* the parsed scale rather than
replace it — and to notice a rescaled or re-plotted sheet where the two differ.

**(b) Raster view — traced contour as a size witness.** Extract the embedded
image (`doc.extract_image(xref)`), trace or threshold it, fit primitives:

| method (on the Ø35 hole, 186 DPI, sheet scale 1:2 → 3.665 px/mm) | diameter |
|---|---|
| white-region blob, `cv2.minEnclosingCircle` | 34.10 mm |
| `cv2.HoughCircles` | 33.78 mm |
| Kåsa fit to the dark stroke pixels (stroke centreline) | 34.78 mm, residual σ 0.15 mm |
| `vtracer` white-region path, sampled 400×, Kåsa fit | 34.43 mm |
| `vtracer` stroke-region path, sampled 400×, Kåsa fit | **35.00 mm** |

and on the same view's outline: traced 552.4 × 369.4 px → **150.7 × 100.8 mm**
against printed 150 × 100.

The lesson is in the spread, not the best number: *which boundary of the ink you
fit* moves the answer by ~1 mm, so a traced measurement is a ±1.5 % witness. It
distinguishes 108 from 100 (7 %). It does not distinguish 35.0 from 35.5, and it
must never be printed as a dimension.

---

## What this would mean for FreeCadCopilot

### Concrete proposal

Keep `sheet_reader`'s contract exactly as it is — *evidence, never conclusions;
first value wins; conflicts recorded, not resolved* — and add one new kind of
evidence: **a measurement, in millimetres, attached to a printed string.**

**Phase 0 (no new dependency, ~150 lines, all inside `sheet_reader.py`)**

1. `_spans(page)` — merge collinear `"l"` items into dimension-line spans
   (angle bucket 1°, offset bucket 1 pt, join gap 60 pt). This is a sibling of
   the existing `_chained()`, which deliberately breaks at junctions; spans
   deliberately *bridge* the text gap instead.
2. `_scale_by_consensus(spans, words)` — vote on `printed / span`, return the
   winning `k`, its consensus set and its size. Report it next to the
   title-block scale in `Sheet.notes`; **flag disagreement instead of
   silently preferring one.**
3. `Printed` gains an optional `measures: float | None` and `off_by: float |
   None`. A printed `108` whose span says 107.18 arrives as
   `108 (the sheet measures 107.2 mm here)`. A `100` that the model proposes
   later can then be contradicted by evidence already on the record.
4. Feed the dash pattern through: a span drawn long-dash-dot is a **centre
   line**, i.e. an axis/symmetry plane, and should be reported as such rather
   than as one more "line from A to B" competing for the 120-entity budget in
   `_drawn()`.
5. Say, in `Drawn` and in the docstring, that on picture-bodied views the
   entities are *annotation geometry*, and that 186 DPI is the ceiling for the
   view body no matter what DPI the page is rendered at.

**Phase 1 (one new dependency: `vtracer`, MIT)**

6. For each view whose body is a placed picture — and for raster attachments,
   where every view is — extract the image, trace it, fit primitives with a
   greedy line/arc pass (reuse `_fitted()`, which already does exactly this on
   PDF polylines: it is the same algorithm, fed traced points instead of PDF
   points), and report **contours in millimetres** using the consensus `k`
   plus the image's own px→pt factor.
7. Report each fitted primitive with an explicit uncertainty
   (`circle Ø35.0 ±0.5 at (61.0, 47.5)`), and — the point of the whole
   exercise — a `nearest printed value` field: the printed string the
   measurement corroborates, or `none`, never a number invented from pixels.

**Phase 2 (no new dependency, needs FreeCAD)**

8. When the description is complete, build the sketch through
   `SketchObject.addGeometry` + `addConstraint` with the *read* values and check
   `solve() == 0` and `DoF == 0`. Non-zero DoF, `ConflictingConstraints` or
   `RedundantConstraints` is a reading defect with a specific index attached —
   feed it back into `design.conflicts` rather than resolving it.

**Phase 3 (opportunistic)**

9. If a `.dxf` is ever offered, take it: `ezdxf` (MIT) turns the entire
   association problem into `dim.dxf.defpoint2/defpoint3` lookups. Detect the
   extension and say so in the UI — "a DXF of this sheet would remove the
   guesswork" is a legitimate thing for the workbench to tell a user.

### Rejected options, and why

- **"Trace the sheet and build from the trace."** ±1.5 % on a 186 DPI bitmap is
  0.5 mm on a 35 mm hole and 2 mm on a 150 mm plate; the sheet's own text layer
  is exact. Tracing is a *witness*, never a source.
- **`PNG → SVG → FreeCAD importSVG → Draft.make_sketch`.** Measured above:
  550 Bézier edges, 616 auto-constraints, 5206 DoF. Auto-constraint on traced
  splines produces noise, not intent.
- **`potrace` / `pypotrace`.** Technically fine and it even has a DXF backend,
  but GPL-2 against an LGPL workbench. `vtracer` (MIT) does the same job here.
- **Bumping render DPI to fix the views.** Cannot work: the view bodies are
  186 DPI bitmaps inside the PDF. (It still helps the *text*, which is why the
  existing crop logic stays.)
- **`toBiArcs()` per traced edge.** Verified: returns a single `LineSegment` per
  tiny Bézier. Global fitting only.
- **Colour as a semantic key** (green = dimension). Works on these four
  SolidWorks sheets, is a style setting, will break on the fifth sheet.
  Use it to cluster, never to classify.
- **Training or fine-tuning a drawing-parsing model** (the arXiv line of work
  above). Right answer at scale, wrong answer for a workbench that must install
  from pip and run offline on a Mac.
- **DWG support.** No open library; the ODA spec is a reverse-engineering
  document, not an API. Ask for DXF.

---

## Open questions

1. **Do other CAD exporters emit true vector views?** Every sheet here is
   SolidWorks-with-shaded-views. An Inventor/Creo/AutoCAD PDF plausibly emits
   real vector edges — and then `get_drawings()` would return `"c"` Bézier items
   and the fitting problem changes shape (Bézier→arc, not pixels→arc). Untested:
   we have no such sample. Worth collecting one before generalising.
2. **How far does span-merging generalise?** The 60 pt join gap and 12 pt
   text-proximity threshold were tuned on six pages. What happens on a densely
   dimensioned sheet where two parallel dimension lines are 8 pt apart?
3. **Arrowheads.** They are present as fill paths (`type == "f"`, e.g. 3 fill
   paths on the 2023 sheet, one of which contains 76 items — every arrowhead on
   the sheet in a single path object). Splitting that path into individual
   triangles would give arrow *tips*, which are better anchors than span
   endpoints and would fix the −0.2 … −0.8 mm systematic bias. Not attempted
   here.
4. **Diameter vs radius vs length.** The consensus test cannot tell a Ø35 from a
   35 mm span; `Ø` is not in the text layer at all (already documented in
   `sheet_reader`). Recovering the Ø glyph from its drawn geometry — a circle
   with a slash, at a known offset from the string — is an unexplored but
   apparently tractable target.
5. **Does `DoF == 0` actually discriminate?** The Sketcher check in Phase 2 is
   proposed on the strength of the API, not on a trial. It needs one experiment:
   build the 2023 part's sketch twice, once with 108 and once with 100, and see
   whether the wrong one is detectable as anything other than "a different
   shape".
6. **Section views and hatching.** The 2851- and 3502-segment pages are mostly
   hatch strokes. Hatch is a *semantic* signal (this face is cut) that the
   current entity budget discards as short lines. Detecting a hatch region
   (parallel equidistant short segments) is cheap and would let a section view
   say what it is.

---

*Sources fetched and code executed on 2026-08-10. FreeCAD 1.1.3, PyMuPDF 1.28.2,
vtracer 0.6.15, OpenCV 5.0.0, scikit-image 0.26, macOS 26.6 arm64.*
