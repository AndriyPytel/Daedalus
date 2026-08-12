# Raster-to-vector tooling for engineering drawings

Research note. Sources are primary (official docs, source files, arXiv/DOI records, vendor
product pages) and are linked inline. Numbers labelled **measured** were produced by running the
tool on this repo's own `tests/modelmania` sheets on this machine (macOS, Apple Silicon) during
this research; nothing in the repo was modified.

## Question

What is the state of the art for turning a raster engineering drawing (PNG / scan / photo) into
vector geometry — and how good is it really? Specifically: which tools do **centreline** tracing
rather than outline tracing, which emit **true arcs and circles** rather than polyline or Bézier
approximations, how do they behave on dashed centrelines, hatching, arrowheads, text and dimension
lines, and what happens to a photograph of a paper drawing?

---

## Executive summary

1. **Tracing accuracy is not the problem; representation is.** At the 186 DPI the modelmania sheets
   actually carry, a traced contour of the Ø35 bore fits a circle to **0.30 px RMS / 0.78 px max
   (0.21 mm)** and yields **Ø34.87 mm against a nominal 35** (measured, §1). Every general-purpose
   tracer is this accurate. **None of them emits a circle.** potrace, autotrace and VTracer all
   produce lines + cubic Béziers only (measured, §2); potrace's DXF "arcs" are bulge segments whose
   individual radii scatter 52–125 px around a true R=81 px circle (measured by the tracer slice).
2. **You do not need a tracer.** `cv2.findContours(CHAIN_APPROX_NONE)` plus a **global least-squares
   circle test per closed contour** separates circles from non-circles by three orders of magnitude
   of residual (0.21 mm vs 12–22 mm, measured, §1) and hands back centre + radius directly. That is
   strictly more useful than any SVG a tracer would give you, and it is ~40 lines.
3. **`cv2.ximgproc.EdgeDrawing`** (Apache-2.0, in the `opencv-contrib-python` wheel, no build step
   on Apple Silicon) is the one off-the-shelf component that returns **validated circles with centre
   and radius** as well as chained edge runs and straight segments — Ø34.85 mm on the same bore
   (measured by the CV slice, §3).
4. **Centreline vs outline is the wrong axis for *this* input.** The modelmania views are embedded
   **shaded colour renders** (yellow fill, red highlight bands, ~2 px black outline), not line art;
   the part's edges are boundaries between filled regions, and outline tracing of the black stroke
   gives two concentric contours whose *midline* is the edge. Centreline tracing matters for a real
   scan of a printed drawing, and there the honest finding is: **all gradient line detectors
   (LSD/FLD/EDLines) are edge detectors — every stroke comes back as two anti-parallel segments;
   pair and midline them. Do not skeletonise** (measured: 122–135 spurs on a simple test sheet;
   every filled arrowhead becomes a Y-junction).
5. **The repo's own `_fitted()` cannot be pointed at a pixel contour unchanged** (measured, §4). Its
   greedy in-order fit with a 0.05 mm tolerance shatters the bore into 15–33 fragments and, with
   pre-smoothing, invents wrong-radius arcs (R16.87 for a true R17.4, R130 "arcs" along straight
   edges). A **global fit-then-verify** pass is required before the existing fitter is useful.
6. **The scale figure in the brief is wrong and should never be hard-coded.** The dimension *line*
   overshoots the extension lines; the extension lines are the truth. `212.60 pt / 150 mm =
   1.41733 pt/mm` — exactly the sheet's stated `1:2` — giving **3.661 px/mm, not 4.54** (measured
   independently twice, §1, §5). Self-calibrating from one dimension makes the number a *derived*
   quantity with a built-in self-check.
7. **Association is the easy part, and it is geometric.** The vector extension-line endpoints for
   the `150` land **0.06–0.36 pt (< 1.3 px)** from the raster-traced contour edge (measured, §5).
   Nearest-primitive matching in page points with a ~0.4 pt tolerance solves it. No ML needed.
8. **"108 read as 100" is not an OCR failure.** Apple Vision (`VNRecognizeTextRequest`, on-device,
   free, offline) read every dimension value on the sheet at confidence 1.000, and over a
   4-rotation union **invented zero digit tokens that were not on the sheet** (measured, §6). Its
   real failures are `Ø → Ф/P` and arrowhead glyphs swallowed into the string. The published number
   for the VLM failure is 75–88 % on dimension reading (MechVQA, §7).
9. **No ML method today turns a raster mechanical view into metric arcs.** Deep Vectorization
   (MPL-2.0) emits lines + quadratic Béziers and requires text-free input; the CAD-sketch
   parameterisers (Vitruvion, PICASSO) do emit `line/arc/circle` tokens but at **6-bit quantisation
   on 128×128** — a ~1.6 mm grid on a 100 mm view, ~7× coarser than what the raster already
   supports — at ~0.75 token accuracy on clean isolated sketches (§7).
10. **For a photo, use a 4-point homography off the drawing frame**, not a learned dewarper. Every
    learned dewarper is built for curled book pages, fitted to text lines, and **none reports a
    metric-mm accuracy** — only MS-SSIM/OCR CER; DocTr/DocTr++ are additionally "All Rights
    Reserved, non-commercial" (§8).

---

## 1. Measured: what the sheets actually contain, and what a contour gets you

The five views of `tests/modelmania/mm25-p1-1.pdf` are embedded PNGs at 616×245 … 712×585 px, all
placed at 186 DPI. They are **shaded SolidWorks renders**, not line art: flat yellow fill, red
bands, ~2 px antialiased black outlines. The sheet's own note — `R2 TYP` / `ALL RED FILLETS` —
means **the colour is semantic**: red marks exactly the faces the R2 fillet note applies to. A
colour-region segmentation of these views therefore recovers a labelled feature set, which is a
capability the vector path does not have at all.

Scale, derived rather than assumed (§5): **3.6603 px/mm** (1 px = 0.273 mm).

Binarise `max(R,G,B) < 100`, `cv2.findContours(..., CHAIN_APPROX_NONE)`, then fit a circle by the
algebraic (Kåsa/Taubin-style) least squares to **each whole closed contour** and look at the maximum
residual:

| contour (front view, `view3`) | pts | global circle fit | max residual | verdict |
|---|---|---|---|---|
| part silhouette | 1814 | r = 263.9 px | 80.6 px = 22.0 mm | not a circle |
| inner rectangle | 1426 | r = 206.3 px | 45.9 px = 12.5 mm | not a circle |
| bore, outer stroke edge | 365 | **Ø 35.12 mm** | 0.67 px = 0.18 mm | **CIRCLE** |
| bore, inner stroke edge | 358 | **Ø 34.61 mm** | 0.73 px = 0.20 mm | **CIRCLE** |

Stroke midline: **Ø 34.87 mm** against the text layer's `35 THRU` — **−0.13 mm, 0.4 %**. The
separation between "circle" and "not a circle" is 0.2 mm vs 12–22 mm, i.e. the test is decisive and
needs no tuning. The same test on the top view (`view0`) finds Ø 44.16 mm and **Ø 39.68 mm** — the
latter against the sheet's `40` (−0.8 %). Silhouette height comes out **100.004 mm** against the
`100` in SECTION B-B.

Fillets: growing straight runs and circular runs greedily over the silhouette contour recovers
4 straight edges plus corner arcs of **R 1.69 / 1.80 / 2.27 mm** against the nominal `R2` — usable,
but the short (≈7 px) arcs are where the fitting is weakest, and the CV slice confirms EDCircles
does not find arcs that short either. **Short-arc segmentation is the one genuinely unsolved piece.**

Resolution and compression, measured by the CV slice with EDCircles on the same bore: JPEG q=20
moves the diameter by **0.002 mm** (non-issue); the floor is set by *stroke width*, not by the fit —
at 93 DPI the 2 px stroke collapses to 1 px and long-line detection drops from 16 segments to 2.
**Stay ≥ 150 DPI.**

## 2. General-purpose tracers: measured, and why none of them is the answer

| tool | licence | offline macOS | output grammar (measured) | centreline? | arcs? |
|---|---|---|---|---|---|
| **potrace 1.16** <https://potrace.sourceforge.net/> | GPL-2.0-or-later (dual licence sold, price unpublished) | yes, `brew`/`pip install potracer`, CLI | 7 closed curves, corners + cubic Béziers; SVG uses `M m l c z`, **zero `A`** | **no — outline only**, every line arrives doubled | no. Its DXF backend emits *bulge* polyline segments; a true R=81 px circle became **27 bulge-arcs of radius 52.4–125.0 px (σ 15.8)** |
| **autotrace 0.31.10** <https://github.com/autotrace/autotrace> | LGPL (library) | yes, `brew`, CLI | 22 open paths, `M L C` only; DXF backend is **R12 POLYLINE/VERTEX only** — measured 22 POLYLINE / 5023 VERTEX / **0 ARC** (source: `output-dxf.c`) | **yes, `-centerline`** — the only free packaged centreline tracer | no |
| **VTracer 0.6.15** <https://github.com/visioncortex/vtracer> | MIT | yes, PyPI wheel, Python API | measured on `view1.png`: 36 paths, command histogram `{M:41, C:550, Z:41}` — **cubics only, no `A`**, and it traces the image border as a path | no — colour-region outlines | no |
| **Inkscape Trace Bitmap / Centerline Tracing** <https://inkscape.org/> | GPL | yes, GUI | — | Centerline Tracing **is autotrace** (`src/trace/inkscape-autotrace.cpp` sets `opts->centerline`) | no |
| **ImageMagick** | ImageMagick licence | yes | — | **not a vectoriser**: `configure.ac` sets `TraceEncodeDelegateDefault='potrace'` — it shells out | — |
| **Adobe Illustrator Image Trace** | proprietary | no headless CLI | Béziers | no | no |
| **Vectorizer.AI** <https://vectorizer.ai/> | cloud SaaS | **no — cloud only** | SVG/DXF, has a DXF "Lines & Arcs" mode and fits parameterised circles/ellipses | **its own docs: "We do not currently support centerline tracing"** for "CAD drawings, technical diagrams" | yes, for outlines |

Notes that matter:

- **There is no Inkscape command line for tracing.** `actions-paths.cpp` exposes no trace action;
  GitLab issue #3450 was closed unactioned. Inkscape is pure dependency weight here.
- **Geometric accuracy of all three free tracers is fine** — sub-0.1 px RMS off a perfect circle on
  the embedded raster. The bias that remains comes from *where you threshold the antialiased
  stroke*, not from the tracer.
- **Pre-binarisation matters more than the tracer choice**: on the colour render autotrace produced
  1479 junk paths; on the same image hard-binarised, 22 clean ones.
- **On a full sheet the output is unusable as evidence**: `autotrace -centerline` on a 200 DPI
  render of the whole drawing emits **one `<path>` with 2072 commands** containing part geometry,
  hatching, arrowheads, dimension lines, border and text skeletons fused together, with no grouping,
  no layers and no primitive identity. Text becomes glyph skeletons (`SECTION B-B` → `SECTION 8-θ`).

Runtime is a non-issue throughout: potrace 0.02 s, autotrace 0.26–0.65 s, VTracer 0.014 s.

## 3. Build-it-yourself CV: what to use and what to reject

- **`cv2.ximgproc.EdgeDrawing`** — header:
  <https://raw.githubusercontent.com/opencv/opencv_contrib/4.x/modules/ximgproc/include/opencv2/ximgproc/edge_drawing.hpp>
  ("Class implementing the ED (EdgeDrawing), EDLines, EDPF, EDCircles and ColorED algorithms").
  Apache-2.0, ships in the `opencv-contrib-python` wheel, imported on Apple Silicon with no build.
  One `detectEdges()` gives `getSegments()` (dense chains), `detectLines()` → `Vec4f`, and
  `detectEllipses()` → `Vec6d` with centre and radius. Measured: **Ø34.85 mm** on the Ø35 bore
  (−0.15 mm) and **Ø34.975 mm** on a synthetic control. Papers: EDLines, Akinlar & Topal, *Pattern
  Recognition Letters* 32 (2011) 1633–1642, DOI 10.1016/j.patrec.2011.06.001; EDCircles, *Pattern
  Recognition* 46 (2013) 725–740, DOI 10.1016/j.patcog.2012.09.020 (both paywalled — metadata read
  via Crossref, full text not consulted).
  **Caveat, verify it yourself:** the header documents the `Vec6d` third field as "perimeter" for
  circles; measurement says it is the **radius** (63.803 → 34.85 mm; a perimeter would read ~401).
- **LSD (`cv2.createLineSegmentDetector`) — pin your OpenCV version.** Licence history sourced to
  the commits: originally AGPL from IPOL → removed in PR #13942 (2019) → restored under Apache-2.0
  in PR #20797 (2021) → PR #29349 (open) moves it to contrib in 5.x. Measured present and working in
  opencv-python 5.0.0, 70 segments on `view1`.
- **All of LSD / FLD / EDLines are *edge* detectors.** Each drawn stroke returns **two anti-parallel
  segments**. Pair them and take the midline: measured **149.95 mm** on a 150 mm edge (−0.05 mm),
  and the pairing distance gives you stroke width — which is a line-type classifier for free.
- **Reject `approxPolyDP`** as a vectoriser: no arc primitive exists in it, and it reduced the
  silhouette to 8–10 points. This is exactly the "polyline approximation destroys the reading"
  failure the project is trying to avoid.
- **Reject `HoughCircles`** — the OpenCV docs themselves say *"it may fail to find correct radii"*,
  and it needs a radius prior. The radius is the answer we want. Measured on `view1` it returned
  r = 62.5 px where the least-squares fit says 63.7 — a 0.6 mm error, 4× worse, for a tuned prior.
- **Reject skeletonisation for geometry.** Measured on a test sheet: 122–135 spurs, up to 564
  junction pixels, a 5× spread between implementations, and every filled arrowhead becomes a
  three-spur Y. Use `cv2.distanceTransform` for stroke width instead.
- **Reject Kornia** — 2 GB of PyTorch, no arc primitives, nothing OpenCV does not already do.
- **ELSED** (<https://github.com/iago-suarez/ELSED>, Apache-2.0, *Pattern Recognition* 127 (2022)
  108619) is faster but needs a git build; our images are 0.2 MP, so speed is irrelevant. Skip.

**Dashes, hatching, arrowheads (measured on a synthetic 186-DPI sheet with ~10 real straight edges):**
LSD returns 187 segments, FLD 191, EDLines 175 — hatching and dash-dot chains account for
essentially all of the excess. A single dash-dot centreline shatters into 22 (LSD) / 23 (FLD) /
28 (EDLines) fragments, while **`HoughLinesP` with `maxLineGap ≥ 10` recovers it as one exact
549 px segment**. So: gradient detectors for geometry, a separate collinear-merge pass for dashed
lines — and the dash *pattern* (long-short-long = centreline, uniform = hidden line) is a free,
highly informative classification signal. Hatching is best deleted before detection via the dominant
angle histogram of short parallel equally-spaced strokes. EDCircles ignores arrowheads (good); the
line detectors return the two long sides of each triangle.

## 4. Measured: the repo's existing `_fitted()` is not directly reusable on pixels

Feeding the traced contours of `view1` into `core.sheet_reader._fitted()` unchanged:

| input | tolerance | result |
|---|---|---|
| raw contour (365 pts, the bore) | 0.05 mm | **33 entities** — the circle is confetti |
| raw contour | 0.15 mm | **15 entities** — still confetti |
| `approxPolyDP(0.6 px)` pre-smoothed | 0.15 mm | 19 entities, and the arcs are **wrong**: `R16.87`, `R16.67`, `R12.48`, `R4.25` for a true R 17.4 circle; straight edges come back as `arc R130.57` |

The cause is structural, not a tuning miss: `_fitted()` is a **greedy, in-order, local** fitter
written for exact PDF polylines, and pixel quantisation is ±0.5 px = ±0.14 mm noise, so a local
window can never distinguish a large arc from a line. The fix is a **global fit-then-verify** stage
in front of it: for each closed contour, first test a whole-contour circle (§1, decisive); otherwise
split at high-curvature points and fit each run globally, accepting on max residual ≈ 0.3 mm. Only
then is the existing line/arc vocabulary the right output shape.

## 5. Measured: scale and association

The `150` dimension on the front view, taken from `page.get_drawings()` (blue, `(0,0,1)`):

```
extension line   (108.70, 68.00)-(108.70, 34.80)     33.20 pt
extension line   (321.30, 68.00)-(321.30, 34.80)     33.20 pt
dimension line   (108.70, 40.40)-(204.50, 40.40)     95.80 pt  } split around the text
dimension line   (321.30, 40.40)-(225.50, 40.40)     95.80 pt  }
arrowhead        (108.70, 40.40)-(117.20, 38.30)      8.76 pt
text '150'       bbox x 207.2 .. 222.9  (sits exactly in the 204.50 .. 225.50 gap)
```

- **Scale**: `212.60 pt / 150 mm = 1.41733 pt/mm`, i.e. `2.8346456/2` to five figures — the sheet's
  `1:2` recovered from its own geometry. Through the image placement rect that is **3.661 px/mm**.
  The 1.7573 pt/mm in the brief comes from the **dimension line's total length (263.62 pt)**, which
  overshoots the extension lines by ~24 %. Dimension-line length is not a measurement; **extension
  lines are**. Corollary: one dimension + its text value calibrates the whole sheet, so the number
  should be derived per sheet and never hard-coded.
- **Association**: mapping the traced contour's stroke-centre edges back into page points gives
  109.055 pt vs the extension line's 108.70 (**0.355 pt = 1.3 px**) and 321.363 vs 321.30
  (**0.063 pt = 0.2 px**). Nearest-primitive matching in page points at a ~0.4 pt tolerance solves
  association outright. Arrowhead direction disambiguates inner vs outer.
- **A free self-check**: the raster-traced span measures 149.80–150.0 mm against the vector
  dimension's exact 150.00. Any traced feature disagreeing with its associated dimension by more
  than ~0.5 mm is a *detectable* tracing error. That, not better OCR, is the guardrail against
  "108 read as 100": it makes a wrong number impossible to accept quietly.

## 6. Measured: OCR for the case with no text layer

Apple Vision `VNRecognizeTextRequest` (<https://developer.apple.com/documentation/vision/vnrecognizetextrequest>,
macOS 10.15+, on-device, free, offline; driven here through `pyobjc-framework-Vision`,
`recognitionLevel = .accurate`, `usesLanguageCorrection = false`) on the rendered sheet:

| render | numeric tokens found (of the 15 in the text layer) | missed |
|---|---|---|
| 143 DPI | 10 | `10`, `1:2`, `01-01-25`, list markers |
| 186 / 300 / 600 DPI, upright | 9 | `10`, `35`, `40`, `1:2` |
| **union over 0°/90°/180°/270°** | **12** | `40`, and the list markers `1.` `2.` |

Two findings:

- **More DPI does not help; more *orientations* does.** Vision rotation-normalises per text region
  but still misses rotated dimension text upright; rotating the page and taking the union is the
  cheap fix.
- **It invented nothing.** Across all four rotations, the set of digit tokens read that are *not* in
  the PDF text layer was **empty**. OCR here fails by omission, never by substitution — the opposite
  failure mode from the VLM. Independently, the CV slice's Swift driver read `150`, `R2 TYP`,
  `(R30)`, `Ø35 THRU`, `20`, `50`, `100`, `SECTION A-A/B-B` at **confidence 1.000** with **zero
  digit errors**; its actual failures were `Ø → Ф (U+0424)` / `Ø → P` (Tesseract 5.5.3,
  Apache-2.0) and arrowhead glyphs swallowed into strings (`→104`, `- 50 -`). Tesseract was noisier
  and offers no confidence-1.0 signal to filter on.

Prior art for separating text from line work: Fletcher & Kasturi's connected-component text/graphics
separation (1988) and Tombre et al.'s refinements remain the standard reference (both paywalled;
read via Crossref metadata only).

## 7. Machine learning: what exists, and why none of it lands

- **Deep Vectorization of Technical Drawings** — Egiazarian, Voynov et al., ECCV 2020, Skoltech;
  <https://arxiv.org/abs/2003.05471>, code <https://github.com/Vahe1994/Deep-Vectorization-of-Technical-Drawings>,
  **MPL-2.0**. Outputs "line segments and quadratic Bézier curves" — **no arcs**; input must be
  "cleared of text". On ABC (B-rep projections of mechanical parts): 77 % IoU, mean deviation 0.6 px
  (≈0.16 mm at 3.66 px/mm) but **Hausdorff 19 px ≈ 5 mm worst case**, ~26 min/image with merging,
  Linux/CUDA/conda, weights on Yandex Disk, no macOS evidence. Usable only as a *fallback tracer*
  with your own arc fitting bolted on and per-primitive validation against the raster.
- **CAD-sketch parameterisers are the only family that emits true `line/arc/circle` tokens.**
  PICASSO (<https://arxiv.org/abs/2407.13394>, current SOTA, **no public code**) and Vitruvion
  (Seff et al., ICLR 2022) work on **128×128 images at 6-bit coordinate quantisation** — 64 levels
  across the view, a ~1.6 mm grid on a 100 mm part, roughly 7× coarser than the ±0.25 mm the 186 DPI
  raster already supports — and reach 0.751 (PICASSO) / 0.537 (Vitruvion) token accuracy on *clean
  isolated* sketches. Precision-destroying and not accurate enough.
- **DiffVG / LIVE / Im2Vec — reject.** They optimise *appearance*, not measurable geometry; LIVE's
  own README describes closed filled Bézier paths, i.e. outline, not centreline; PICASSO reports
  DiffVG producing "degenerate geometries" when pushed at CAD primitives.
- **Rejected on packaging/licence, with sourced reasons**: PolyVector Flow (SIGGRAPH Asia 2021)
  needs a **Gurobi** licence to build; Virtual Sketching is TF 1.x and non-deterministic by design;
  Fidelity-vs-Simplicity ships a Windows binary with no source; PolyVector Fields (TOG 2019) is
  25 h/image with 43k primitives; CAD-Recode's weights are **CC-BY-NC-4.0** (unshippable) and it
  consumes point clouds anyway.
- **Worth stealing as *formulations*, not models**: FloorPlanCAD's **panoptic symbol spotting**
  (<https://arxiv.org/abs/2105.07147>) annotates per **vector entity** — exactly the shape of our
  green/blue dimension apparatus; Digitize-PID (<https://arxiv.org/abs/2109.03794>) gives the
  detect → associate → validate-with-domain-rules skeleton; CAD-Assistant
  (<https://arxiv.org/abs/2412.13810>) is architecturally the same bet as FreeCadCopilot (a VLLM
  planner over a CAD Python API) and reports that the **serialisation format** of geometry into the
  prompt materially changes accuracy — directly actionable for how `shown(sheet)` presents entities.
- **The number behind "108 read as 100"**: MechVQA reports Dimension & Annotation reading accuracy
  of 84.99 (GPT-5), 87.74 (Gemini 3 Pro), 78.65 (Claude Sonnet 4.5), and only 54–60 on
  projection/multi-view reasoning. 15–25 % of dimension reads are wrong. Never let the model read a
  number the text layer already has exactly.

## 8. Photo of a paper drawing

- **Use `cv2.getPerspectiveTransform` + `warpPerspective` off the drawing frame.** An engineering
  drawing has what no book page has: a rectangular title-block border of known proportions and
  dimension values that let you *verify* the rectification. Four frame corners → homography → check
  a known dimension.
- `VNDetectDocumentSegmentationRequest` (macOS 12+) returns the quad for free on a Mac; an optional
  accelerator only, since it does not exist on Linux/Windows FreeCAD.
- **page_dewarp** (mzucker, **MIT**; maintained fork `page-dewarp` 0.3.4 on PyPI) is classical and
  pip-installable, but its cubic-sheet model is fitted to **spans of text lines** — a mechanical
  drawing gives it almost nothing to fit in the middle of a view. (Argument from its documented
  method; not run.)
- **Learned dewarpers — reject.** DocUNet (CVPR 2018), DewarpNet (ICCV 2019, MIT), DocTr (ACM MM
  2021) and UVDoc are all built for curled book pages and evaluated on the DocUNet benchmark with
  **MS-SSIM and OCR CER — no paper in that line reports a metric mm accuracy**, which is the only
  thing that matters when the output feeds a dimensioned model. They also resample the image through
  a learned grid, which is precisely what you do not want under a measurement. **DocTr's licence is
  "Copyright © Hao Feng 2024. All Rights Reserved", non-commercial only** — unshippable.
- Binarisation under uneven lighting: `cv2.ximgproc.niBlackThreshold` (Sauvola/Niblack), with the
  caveat that local thresholding *moves edges* and therefore biases measurements.

## 9. Commercial raster-to-CAD

- **Scan2CAD** (<https://scan2cad.com/>, macOS 14.8+, $89/mo Pro, $1599/yr Business) is the only one
  built for exactly this problem and the only one with a **published Python API**
  (<https://developers.scan2cad.com/python>). Its `Preset.Technical` / `Mechanical` exposes
  `arc_identification` + `arc_min_radius`, `circle_identification`, `arrow_identification` +
  `arrow_max_head_length`, `dash_line_identification`, `hatch_identification`, `gap_jump_distance`,
  orthogonal snapping, OCR, and `Vector.scale(px, length, unit)`. It cannot be a dependency of an
  open workbench (per-seat licence, API is Business-tier, driven by launching the GUI with a `.s2c`
  script), **but that settings list is the best available specification of what to build**, and no
  independent accuracy benchmark for it exists publicly.
- The rest — WinTopo, Vextractor, RxSpotlight, Able R2V, VectorNow, the pdf2dxf converters — are
  per-seat and Windows-only. Not adoptable.

---

## What this would mean for FreeCadCopilot

### Proposed pipeline (raster views, additive to `core/sheet_reader.py`)

1. **Extract the embedded rasters at native resolution** (`page.get_image_info(xrefs=True)` +
   `pymupdf.Pixmap(doc, xref)`), keeping each image's placement rect. Do not re-render the page: at
   186 DPI the placed image *is* the ceiling.
2. **Derive the scale, per sheet, from the vector dimension apparatus** — extension-line span in
   points ÷ the dimension's text value — and record it. Never hard-code pt/mm, and never measure it
   off dimension-line length (§5).
3. **Binarise off the ink, not off brightness** (the existing `INK` convention), then
   `findContours(CHAIN_APPROX_NONE)`. Optionally keep the **colour clusters** as well: on these
   sheets red = the faces the `R2 TYP ALL RED FILLETS` note applies to.
4. **Global fit-then-verify**: for each closed contour, test a whole-contour circle (accept at
   max residual ≲ 0.3 mm — measured separation is 0.2 mm vs 12 mm); otherwise split at curvature
   maxima and fit each run globally. Pair concentric contours (the two edges of one stroke) and
   report the midline. **Only then** hand the runs to the existing line/arc vocabulary.
5. **Optionally cross-check with `cv2.ximgproc.EdgeDrawing.detectEllipses()`** — one extra
   pip package (`opencv-contrib-python`, ~90 MB, Apache-2.0, arm64 wheel, milliseconds per view) that
   returns validated circles independently. Agreeing detectors are a confidence signal; disagreeing
   ones are a `conflict` in `design.py` terms.
6. **Associate geometrically**: match each vector extension-line foot to the nearest traced primitive
   in page points (~0.4 pt tolerance, measured), use the arrowhead direction for inner/outer, and
   attach the text-layer string. Report `"the 35 measures this circle, Ø34.87 traced"` — evidence
   plus its cross-check, never a resolution.
7. **Raise a `conflict` when a traced measurement and its associated dimension disagree by more than
   ~0.5 mm.** This is the direct structural answer to "108 read as 100": the model can still misread,
   but the misreading now contradicts geometry the pipeline measured itself.
8. **True raster-only sheets (PNG screenshot, scan, photo)**: same pipeline, plus a 4-point homography
   off the drawing frame for photos, plus **Apple Vision OCR over four rotations, union-ed**, with a
   `Ф|P|φ → Ø` post-fix and stripping of leading/trailing `- → •`. Mark OCR strings as OCR-sourced;
   they omit but do not invent.

### Rejected, and why

| rejected | why |
|---|---|
| potrace as the line source | outline-only (every line doubled) and its DXF "arcs" are bulge segments scattering 52–125 px around a true R=81 px circle; GPL-2.0 is also a licence event if linked |
| VTracer / Inkscape / Illustrator / ImageMagick | cubics only, no arcs, no centreline; Inkscape has no CLI trace action; ImageMagick just shells out to potrace |
| autotrace as a *required* dependency | genuinely the only free centreline tracer and LGPL, but it adds a brew-installed binary and an SVG parser to obtain contours that `findContours` already gives us, and it still cannot emit an arc |
| `approxPolyDP` | no arc primitive — exactly the polyline-destroys-the-reading failure to avoid |
| `HoughCircles` | docs concede "may fail to find correct radii"; measured 4× worse than a least-squares fit |
| skeletonisation for geometry | 122–135 spurs measured; every filled arrowhead becomes a Y |
| Kornia / DiffVG / LIVE / Im2Vec | appearance optimisation, PyTorch weight, no arcs |
| Deep Vectorization as the primary path | quadratic Béziers, text-free input required, 26 min/image, CUDA/Linux, 5 mm worst-case Hausdorff |
| PICASSO / Vitruvion | 6-bit quantisation ≈ 1.6 mm grid — coarser than the raster we already have; ~0.75 token accuracy; no code |
| learned dewarpers | built for book pages, no metric-mm evaluation anywhere; DocTr is non-commercial-only |
| Scan2CAD / WinTopo / Vextractor / RxSpotlight | per-seat commercial, mostly Windows-only, cannot be a dependency of an open workbench |
| feeding pixel contours into `_fitted()` unchanged | measured: 15–33 fragments per circle, and wrong radii after smoothing |

### Cost

`opencv-contrib-python` (Apache-2.0, arm64 wheel, ~90 MB) is the only new dependency, and even that
is optional if `findContours` + a hand-written least-squares circle fit is preferred — NumPy alone
suffices for step 4. Runtime is milliseconds per view. Everything else (steps 1, 2, 6, 7) is code in
`sheet_reader.py` using PyMuPDF facilities the module already imports. Apple Vision OCR costs
nothing on macOS but is macOS-only and therefore must degrade, in the module's existing idiom, to
"this sheet has no text layer" elsewhere.

---

## Open questions

1. **Short arcs.** The R2 fillets (≈7 px radius at 186 DPI) come out as 1.69–2.27 mm and EDCircles
   does not find them at all. Arc *segmentation* — where does an arc start and stop along a contour —
   is the one genuinely unsolved piece and needs its own experiment on all four modelmania sheets.
2. **No real scan has been tested.** Every number above is from a clean embedded raster or a
   synthetic sheet. Noise, skew, paper texture and a real 300 DPI scan are untested.
3. **Hatched section views.** The section views on these sheets are shaded renders, not hatched line
   work; the hatching numbers come from a synthetic drawing. Untested against a real hatched section.
4. **`EdgeDrawing.detectEllipses()` `Vec6d` semantics** contradict the header docstring
   ("perimeter" vs the measured radius). Confirm against a known circle before relying on it.
5. **Scan2CAD's real accuracy** is unmeasured publicly. One afternoon with the 14-day trial and the
   `tests/modelmania` sheets would settle whether a commercial engine beats 0.15 mm — that is the
   highest-value single experiment remaining, even if the product is never adopted.
6. **Paywalled primary texts**: EDLines, EDCircles, Fletcher & Kasturi and Tombre et al. were read
   as Crossref metadata only, not full text.
7. **Not run**: page_dewarp, UVDoc, PaddleOCR, docTR, EasyOCR, Deep Vectorization.
