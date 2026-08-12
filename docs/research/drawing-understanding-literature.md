# Drawing understanding: from a 2D engineering drawing to structured geometry and 3D CAD

*Literature and tooling review, primary sources only. Every claim carries the URL of the source that
owns it. Compiled for FreeCAD Copilot; measured facts about this repo's own test sheets are marked
**[measured]** and were taken from `tests/modelmania/*.pdf` directly.*

## Question

How does the research and industrial literature convert 2D engineering drawings into structured,
machine-readable geometry/features — and into 3D CAD? Which of it is usable as an offline component
today, and which is research-only?

## Executive summary

- **No published system reads a dimensioned engineering drawing well enough to trust.** The most
  on-topic paper, Drawing2CAD (ACM MM 2025, MIT), reaches 76.1% parameter accuracy with a **20.3%
  invalidity ratio** on toy DeepCAD parts — and its input SVG contains *no dimensions at all*
  ([2508.18733](https://arxiv.org/abs/2508.18733)). The only work whose input images carry dimensions,
  Ortho2CAD, releases **no weights** ([2607.08891](https://arxiv.org/abs/2607.08891)).
- **Every generative CAD model emits normalised, quantised geometry, never millimetres.** DeepCAD
  normalises into a 2×2×2 cube and quantises to 256 levels ([2105.09492](https://arxiv.org/pdf/2105.09492),
  §3.1.2); CAD-Recode normalises to a unit box at 1-unit resolution over −100..100
  ([2412.14042](https://arxiv.org/pdf/2412.14042), App. B). That is ~0.5% of the bounding box, i.e. **~1 mm
  on a 200 mm part**. Such a model *cannot* resolve 108 vs 100. Generative CAD is a **shape/topology
  proposer**, never a dimension source.
- **We already hold strictly more information than any published model consumes.** [measured] The
  dimension apparatus on our sheets is vector (coloured PDF paths), the dimension text is vector with
  exact bboxes, and the scale is exactly recoverable (1.7573 pt/mm). No paper in this literature exploits
  exact dimension text. That gap is where FCC sits.
- **The real problems are ASSOCIATION and SHAPE, not OCR.** Which number measures which edge; and a
  contour that exists only as 186 DPI pixels ([measured]: views are embedded PNGs at 186 DPI, so
  re-rendering the PDF cannot recover geometry).
- **Vector input beats raster input, empirically.** Drawing2CAD's own ablation on identical data:
  vector 75.14% param-accuracy / 23.40% IR vs raster 70.49% / 29.79%
  ([2508.18733](https://arxiv.org/abs/2508.18733)). Independent second vote: Img2CAD's "Structured Visual
  Geometry" vectorises to a wireframe first ([2410.03417](https://arxiv.org/abs/2410.03417)).
- **The highest-value change costs zero new dependencies:** PyMuPDF `get_drawings` returns typed path
  items plus stroke colour, width and dashes
  ([docs](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings)), so the arrowhead
  rectangles at each end of a dimension line give a measured span in points; ÷1.7573 pt/mm yields an
  **independent numeric prediction to cross-check every text-layer number**. 108 vs 100 is ~14 pt apart —
  far above drafting slop.
- **Cropping is arithmetic, not a heuristic.** Claude tokenises in 28×28 patches with a 1568 px long-edge
  cap (2576 px on 4.7+) and downscales beyond it
  ([Anthropic vision docs](https://docs.anthropic.com/en/docs/build-with-claude/vision)). An A3 sheet at
  186 DPI is ~3070 px → 0.51× → a 25 px dimension glyph arrives at ~13 px. That is a sufficient mechanical
  explanation for "108 read as 100".
- **Apple Vision is a free, on-device, macOS-native second reader** — but only if configured correctly:
  `usesLanguageCorrection = false` ("Disabling this property returns the raw recognition results"), and
  `minimumTextHeight` defaults to **1/32 of image height**, so a full sheet makes Vision silently ignore
  dimension text ([VNRecognizeTextRequest](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)).
  `topCandidates(3)` gives a free confusion set — exactly the `["108","100","10B"]` signal we need.
- **Photo→3D (TripoSR/TRELLIS/PartCrafter/Hunyuan3D/…) is the wrong axis entirely.** Every one outputs a
  mesh/gaussian/implicit field in an arbitrary unitless box: scale is ambiguous by construction, the
  representation has no sketch/extrude/hole/datum, and the licences are a minefield. Skip the whole family.
- **A mesh never becomes a parametric model automatically.** FreeCAD's `Part.Shape.makeShapeFromMesh` calls
  `setFaces` + `sewShape` — one planar face per triangle
  ([source](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Part/App/TopoShapePyImp.cpp)); the
  ReverseEngineering workbench is manual per-primitive fitting
  ([source](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/ReverseEngineering/Gui/Command.cpp)).
  **Mesh outputs are reference geometry only; never on the critical path.**

---

## Findings

### 1. Classic drawing interpretation: dimension sets and text–graphics association

This is the oldest and most directly relevant body of work, and it is *exactly* our setting — a vectorised
drawing plus a dimension apparatus. It is worth reading rather than reinventing, and there is **no released
code**: all of it is pre-2000 and paywalled.

- **Lai & Kasturi, "Detection of dimension sets in engineering drawings", IEEE TPAMI 16(8), 1994** —
  <https://doi.org/10.1109/34.308483> (ICDAR 1993 precursor: <https://doi.org/10.1109/ICDAR.1993.395662>).
- **Das & Langrana, "Recognition and Integration of Dimension Sets in Vectorized Engineering Drawings",
  CVIU 68(1), 1997** — <https://doi.org/10.1006/cviu.1997.0537> (ICDAR 1995:
  <https://doi.org/10.1109/ICDAR.1995.599010>).
- **Dori & Liu, "Sparse Pixel Vectorization: an algorithm and its performance evaluation", IEEE TPAMI 21(3),
  1999** — <https://doi.org/10.1109/34.754586> (ICPR 1996: <https://doi.org/10.1109/ICPR.1996.547280>) — the
  classical raster→vector algorithm designed for engineering drawings specifically.

**The framing to steal.** A *dimension set* is a **group**: {text, dimension/leader line, two arrowheads,
two extension (witness) lines, the measured geometry}. Recognition is (a) grouping those primitives, then
(b) attaching the group to the object edge that the extension lines touch. That decomposition maps one-to-one
onto what `get_drawings` hands us for free (§4.1), which is why the classical algorithms are directly
implementable here even though their raster front-ends are obsolete for our vector input.

**Modern analogue, and the architecture to copy:** *Context-Aware Mapping of 2D Drawing Annotations to 3D CAD
Features Using LLM-Assisted Reasoning* — <https://arxiv.org/abs/2602.18296>. A **deterministic-first**
framework: callouts are scored against candidate 3D features with "an interpretable metric that combines type
compatibility, tolerance-aware dimensional agreement, and conservative context consistency", and it
**escalates to the LLM only when deterministic scoring cannot resolve the ambiguity**, with one
human-in-the-loop review. Reported on 20 real CAD-drawing pairs: precision 83.67%, recall 90.46%, F1 86.29%.
No code stated (unverified beyond the abstract).

**The one learned formulation of association:** *Drawing-Recode* —
<https://arxiv.org/abs/2607.27558>. Raster drawing → image encoder for geometry + a *separate* text-recognition
module for annotations, tied together by an explicit **Annotation Grounding Loss** with cross-attention, then
an LLM emits "Structured Parametric CAD Code". This is the only paper found that treats dimension↔edge
grounding as a first-class objective. **No repo; research-only. Read it for the formulation.**

### 2. View identification and 3D reconstruction from orthographic views

*Partially covered — see "Not covered" below.* The bottom-up wireframe/B-rep line (Idesawa 1973; Wesley &
Markowsky 1981; Sakurai & Gossard; Meeran & Pratt; Company/Contero surveys) was assigned to a sub-agent that
failed on provider connection errors and did not return; its findings are **not** in this document and the
citations above are therefore **unverified here**. What *is* verified is that no modern released system in
§3 attempts multi-view orthographic reconstruction from a real dimensioned sheet.

### 3. Modern ML on vector drawings (symbol spotting)

Verified repository facts (GitHub API, 2026-08):

- **CADTransformer** (CVPR 2022) — <https://github.com/VITA-Group/CADTransformer>, licence **MIT**.
- **SymPoint** "Symbol as Points: Panoptic Symbol Spotting via Point-based Representation" (ICLR 2024) —
  <https://github.com/nicehuster/SymPoint>, licence **NOASSERTION** (i.e. not a recognised SPDX licence —
  treat as unclear).
- **SymPointV2** — <https://github.com/nicehuster/SymPointV2>, **no licence detected**.

**Caveat that decides their usefulness:** this entire cluster (FloorPlanCAD, CADTransformer, GAT-CADNet,
SymPoint, ArchCAD-400K) is trained on **architectural floor plans**, whose primitives arrive as DWG/SVG
vector entities with symbol-class labels. A mechanical sheet has different symbols, different conventions and
different failure modes, and we would have no training data. *Full dataset/licence/domain verification for
this cluster was not completed — see "Not covered".*

The one adjacent result worth acting on: **VectorGraphNET** (<https://arxiv.org/abs/2410.01336>) and
**Component Segmentation of Engineering Drawings using GCNs** (<https://arxiv.org/abs/2212.00290>) both run a
graph network over *already-vector* input, i.e. they solve segmentation, not vectorisation — which is the
half we do not need.

### 4. The offline toolbox that could ship this quarter

#### 4.1 Vector extraction beyond the text layer — the highest-value item in this report

**PyMuPDF `Page.get_drawings(extended=False)`** —
<https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings>. Documented returns: a list of path
dicts with `items`, `rect`, `color` (stroke), `fill`, `width`, `dashes`, `type` (`"f"` | `"s"` | `"fs"`),
`seqno`, `layer`. Item entries are exactly `("l", p1, p2)` line, `("c", p1, p2, p3, p4)` cubic Bézier,
`("re", rect, orientation)` rect, `("qu", quad)`. Since v1.19.1 `color`/`fill` "always are either RGB tuples
or None", so **filtering the green dimension paths by colour is a documented, stable comparison, not a hack**.
`Page.get_cdrawings()` is "functionally equivalent … but much faster" (2 s → 200 ms per the docs).

**Arrowheads.** A solid arrowhead is a small **filled** path (`type` `"f"`/`"fs"`) with a triangular `items`
sequence and a `rect` a few points across. So: green paths → split into long thin `("l", …)` strokes
(dimension/extension lines) and small filled triangles (arrowheads) by `type` + `rect` area; each arrowhead's
`rect` centre is a **measurement endpoint**. Two collinear arrowheads at the ends of a dimension line define
the measured span in points; ÷1.7573 pt/mm [measured] gives the value the number *should* be. (The primitives
are documented; the composition is our design.)

**Text ↔ geometry linking.** `page.get_text("words")` returns `(x0, y0, x1, y1, "word", block_no, line_no,
word_no)` (<https://pymupdf.readthedocs.io/en/latest/textpage.html#TextPage.extractWORDS>) in the *same* PDF
user space as `get_drawings` — so association is plain 2D geometry, no ML.

**Licence caution:** PyMuPDF is **AGPL-3.0 or an Artifex commercial licence**
(<https://pypi.org/pypi/PyMuPDF/json>). Already a dependency; relevant when the workbench is distributed.

**pdfplumber** (<https://github.com/jsvine/pdfplumber>, **MIT**) exposes `.lines`, `.rects`, `.curves`,
`.chars` with `linewidth`, `stroking_color`, `dash`, and — uniquely useful — a char `matrix` plus
`pdfplumber.ctm.CTM`, so **rotated dimension-text angle is directly computable** (`ctm.skew_x`). Caveat:
pdfminer.six classifies anything it cannot type as a "curve", so polyline arrowheads may land in `.curves`.

#### 4.2 Recovering ⌀ / R / ° / ± that are drawn as geometry

There is **no library that does this**; no primary source documents a ready-made diameter-symbol detector.
Documented building blocks, cheapest first:

1. **Vector-signature matching.** In `get_drawings`, ⌀ is a closed curve (4 Bézier `("c", …)` items forming a
   circle) plus one straight `("l", …)` crossing it, same stroke colour and `width`, with a `rect` whose
   height matches the adjacent word's cap height and which abuts the number's `x0`. ~50 lines, deterministic,
   no dependency. **Highest value / lowest cost item in the report after §4.1.**
2. **Raster template matching**, `cv2.matchTemplate`
   (<https://docs.opencv.org/4.x/df/dfb/group__imgproc__object.html>) at the known 4.54 px/mm [measured] —
   scale sensitivity, its usual failure mode, is neutralised because the scale is exact.
3. **Ask the VLM only the 1-bit question** ("is there a ⌀ immediately left of '35'?") on a tiny crop, since
   the numeral itself is already exact from the text layer.
4. ML fallback (needs training data we do not have): Faster R-CNN on quality-restored patches
   (<https://arxiv.org/abs/2312.13620>); rotation-aware **YOLOv11-obb** + Donut parsing
   (<https://arxiv.org/abs/2506.17374>) — oriented boxes are the right tool for rotated dimension text.

#### 4.3 Vectorising the 186 DPI line art

- **potrace** (<https://potrace.sourceforge.net/>, **GPL-2.0-or-later**): bi-level input only; backends
  include SVG, PDF, **DXF**, GeoJSON; `-a 0` gives polygon output with no smoothing ("If this parameter is 0,
  then no smoothing will be performed and the output is a polygon"), `-O` trades Bézier joining against
  accuracy. **Critical limitation: potrace is an *outline* tracer** — a 2 px drawn edge returns as a closed
  2 px-wide loop, not one line. `pypotrace` is GPL with no arm64 wheels.
- **autotrace** (<https://github.com/autotrace/autotrace>): advertises "Outline **and centerline** tracing",
  `-centerline`, DXF output. Programme GPL-2+, library LGPL-2.1+. **Centreline is what line art actually
  needs — the better default than potrace here.**
- **vtracer** (<https://github.com/visioncortex/vtracer>, **MIT**): Rust, prebuilt binaries, `--preset bw`
  for line art; outline-only, SVG only. The permissive alternative.
- **OpenCV** (Apache-2.0, arm64 wheel): `HoughCircles`, `HoughLinesP`, `createLineSegmentDetector`
  (note the documented gap — LSD "removed from OpenCV version 3.4.6 to 3.4.15 and version 4.1.0 to 4.5.3 due
  original code license conflict… restored again",
  <https://docs.opencv.org/4.x/db/d73/classcv_1_1LineSegmentDetector.html>), `findContours` + `approxPolyDP`,
  `fitEllipse`. With the exact scale, `minRadius`/`maxRadius` can be constrained from an expected mm range.
- **Apple `VNDetectContoursRequest`** (<https://developer.apple.com/documentation/vision/vndetectcontoursrequest>):
  first-party, on-device, gives a contour **hierarchy** (`childContours` = holes for free), `normalizedPath`,
  and `polygonApproximation(epsilon:)` (Ramer–Douglas–Peucker). **Trap: `maximumImageDimension` defaults to
  512** — leave it and the 186 DPI view is silently downsampled and the geometry destroyed.
- **Deep Vectorization of Technical Drawings** (ECCV 2020, <https://arxiv.org/abs/2003.05471>,
  <https://github.com/Vahe1994/Deep-Vectorization-of-Technical-Drawings>, **MPL-2.0**): learned cleaning →
  transformer estimating vector primitives → optimisation refinement, explicitly for "2D CAD images". README
  requires a **Linux system**; 2020-era PyTorch; weights distributed as Drive zips. Research-grade fallback.
- **Accuracy bound [measured]:** at 0.22 mm/px, ±1 px on each of two opposite edges is **±0.44 mm**.
  Therefore: **never source a dimension from traced pixels — take the number from the vector dimension
  apparatus and let the raster set only topology.**

#### 4.4 OCR offline on Apple Silicon

**Apple Vision `VNRecognizeTextRequest`** — first-party, on-device ("all of Vision's processing happens on
the user's device"), free, ships with the OS
(<https://developer.apple.com/documentation/vision/vnrecognizetextrequest>,
<https://developer.apple.com/documentation/vision/recognizing-text-in-images>). Key documented behaviour:
`recognitionLevel` `.fast` (character detection + small ML model, "similar to traditional optical character
recognition") vs `.accurate` (neural, string/line level); `minimumTextHeight` "relative to the image height…
default 1/32"; `usesLanguageCorrection` — "Disabling this property returns the raw recognition results";
`topCandidates(_:)` → ranked `VNRecognizedText` with `confidence` and `boundingBox(for:)`, whose docs warn
"avoid using their contents for image processing", and whose precision is **word-level in `.accurate`,
character-level in `.fast`** (<https://developer.apple.com/documentation/vision/vnrecognizedtext/boundingbox(for:)>).
Rotation is only supplied as a 90°-step image orientation on the handler — rotate the crop yourself using the
angle known from the PDF text CTM. From Python: `pyobjc-framework-Vision` (**MIT**) or `ocrmac` (**MIT**,
<https://pypi.org/project/ocrmac/>), `OCR('x.png').recognize()` → `[(text, confidence, [x,y,w,h]), …]`.

Alternatives, all verified: **PaddleOCR** Apache-2.0, `paddlepaddle` has macosx_11_0_arm64 wheels — heavy but
viable fallback; **docTR** Apache-2.0, pure-Python wheel, MPS unverified; **Tesseract** Apache-2.0, weak on
sparse rotated digits amid line art; **Surya** code Apache-2.0 but **weights on a modified OpenRAIL-M
(free only under $5M)**, with a documented llama.cpp/Metal Apple-Silicon path; **TrOCR** MIT but *not a
detector* and prone to fluent hallucination on non-linguistic strings like `⌀35 THRU`.

**Calibration on fine-tuning:** a **0.23B Florence-2 fine-tune on 400 annotated drawings beat zero-shot GPT-4o
and Claude-3.5-Sonnet by +52.40% F1 and −43.15% hallucination** on GD&T extraction
(<https://arxiv.org/abs/2411.03707>). Read the other way round: **frontier VLMs zero-shot on drawing
annotations are the weak link** — consistent with the 108→100 bug.

#### 4.5 What resolution actually reaches the VLM (first-party)

- **Anthropic** (<https://docs.anthropic.com/en/docs/build-with-claude/vision>): "Claude views images in
  patches… Each patch is a **28×28-pixel** block"; cost `⌈w/28⌉ × ⌈h/28⌉`. Standard tier: long edge ≤1568 px,
  ≤1568 visual tokens; high-res tier (Claude 4.7+): ≤2576 px, ≤4784 tokens; "Images larger than either limit
  are downscaled before processing". Guidance: "If the image contains important text, make sure it's legible
  and not too small… Consider pre-resizing your images, cropping them, or both." >20 images per request
  triggers a stricter 2000 px per-image limit.
- **OpenAI** (<https://platform.openai.com/docs/guides/images>): legacy `detail:"high"` scales to fit 2048²
  then shortest side 768 px and counts 512 px tiles; newer models expose **`detail:"original"`** (up to 10,000
  patches / 6000 px). Explicit guidance: "If your task requires fine visual detail… such as **optical
  character recognition (OCR)**… set `"detail": "original"`. The `low` and `high` detail levels may resize the
  image before analysis, which can obscure small details."
- **Google Gemini** (<https://ai.google.dev/gemini-api/docs/image-understanding>): 258 tokens if both
  dimensions ≤384 px, otherwise **tiled into 768×768 tiles**; Gemini 3 adds `media_resolution` — "Higher
  resolutions improve the model's ability to read fine text". Best practice: "When using a single image with
  text, **place the text prompt before the image**" — a free prompt-ordering change.

### 5. Generative CAD: is the output a program or a mesh?

**Output classes used below: PROGRAM** (parametric feature program / command sequence / CadQuery / KCL — useful
to us), **B-REP** (solid, no history — importable, not parametric), **MESH** (visual reference only).

**Datasets.** DeepCAD 178,238 sketch+extrude sequences, code **MIT**, data under Onshape's terms
(<https://arxiv.org/abs/2105.09492>, <https://github.com/rundiwu/DeepCAD>) — models with operations beyond
sketch and extrude were "simply discard[ed]", so no fillets, revolves or holes-as-features. Fusion 360 Gallery
8,625 human design sequences, **Autodesk non-commercial research only**
(<https://github.com/AutodeskAILab/Fusion360GalleryDataset>) — the only corpus with real design-intent ordering.
ABC 1M B-reps, copyright retained by creators (<https://deep-geometry.github.io/abc-dataset/>). SketchGraphs
15M sketches **with geometric constraint graphs**, **MIT** (<https://github.com/PrincetonLIPS/SketchGraphs>) —
the only corpus pairing 2D primitives with *constraints*, which is exactly the representation we want per view.

**Drawing → CAD (the on-topic cluster, and it is thin).**
- **Drawing2CAD** (ACM MM 2025, **MIT**, <https://github.com/lllssc/Drawing2CAD>): SVG orthographic views →
  DeepCAD command sequence, dual decoder + soft-target loss. Four views: ACC_cmd 82.43, **ACC_param 76.09,
  IR 20.31**, MCD 10.88. Its raster-vs-vector ablation is the empirical argument for vectorising first.
  **No dimensions in the input at all.**
- **Ortho2CAD** (<https://arxiv.org/abs/2607.08891>, <https://github.com/AdityaJoglekar/Ortho2CAD>, **no LICENSE
  file**): rasterised first-angle ortho drawings "complete with dashed hidden lines and key dimensions" →
  CadQuery, Qwen-VL SFT + geometry-grounded RL on 3D IoU; claims 100% syntactically valid code. **No weights
  released.** Their pythonOCC drawing generator is worth stealing to synthesise dimensioned eval data from STEP.
- **Multi-stage hybrid VLM framework** (ICIEA 2026, <https://arxiv.org/abs/2510.21862>): YOLOv11-det layout →
  **YOLOv11-obb** oriented detection of measures/GD&T/roughness → two Donut OCR-free VLMs. Output is
  structured annotations, **not** a CAD model. Reusable *architecture* for our pre-LLM stage.
- **Photo2CAD** (<https://arxiv.org/abs/2101.04248>): rule-based OpenCV from photos of drawings → **OpenSCAD
  program**. Author's own limit: "does not address hidden lines". Historical interest.

**Image / point cloud → PROGRAM (best published results, wrong input for us).**
- **cadrille** (ICLR 2026, <https://arxiv.org/abs/2505.22914>, <https://github.com/col14m/cadrille>): code
  **Apache-2.0**, weights **CC BY-NC 4.0**. Qwen2-VL-2B class; images/points/text → CadQuery. DeepCAD test,
  multi-view images: **CD 0.18, IoU 86.1, IR 1.5** — the best public image→CAD-program result with released
  weights. Mac-runnable. But its "images" are renders of solids, not annotated sheets; NC weights block shipping.
- **CAD-Recode** (ICCV 2025, <https://arxiv.org/abs/2412.14042>): **CC BY-NC 4.0**. 256 points → CadQuery.
  DeepCAD CD 0.30 / IoU 92.0 / IR 0.4; real scans (CC3D) CD 0.76 / IoU 74.2 **with 10× test-time sampling**,
  and CD 3.05 / IoU 65.6 / **IR 16.8%** without. Authors' own stated limit: "lacks the expressiveness to model
  complex shapes that contain operations beyond the extrusion operation such as revolution and fillet" — i.e.
  exactly the Model Mania class.
- **CAD-Coder** (<https://arxiv.org/abs/2505.14646>, code Apache-2.0, weights licence unverified): 13B LLaVA,
  image → CadQuery; CUDA-shaped setup.
- **Vitruvion** (ICLR 2022, <https://arxiv.org/abs/2109.14124>, **no LICENSE file**): 128 px raster sketch →
  primitives **+ constraint graph**. Image-conditional next-token accuracy 82.2% vs 71.7% unconditional, on a
  64×64 quantised grid (~3 mm on a 200 mm part). **Best conceptual template** for trace → primitives →
  constraints → bind the exact dimension to the constraint. Research-only as code.
- **Free2CAD** (SIGGRAPH 2022, **MIT**, <https://github.com/Enigma-li/Free2CAD>): ordered pen strokes →
  CAD command sequence via group-strokes → fit-operation → feed-back-correction. README states "**The current
  data hosting is broken**". Needs *temporally ordered* strokes, which a drawing does not have. **Take the
  architecture, not the code.**
- **Img2CAD** — two distinct papers, do not conflate: VLM predicts discrete structure + a separate transformer
  regresses continuous parameters (<https://arxiv.org/abs/2408.01437>); and the "Structured Visual Geometry"
  one that vectorises to a wireframe first (<https://arxiv.org/abs/2410.03417>). Both research-only, both
  votes for our plan. The **factorisation** — VLM picks the features, a deterministic fitter picks the sizes —
  is the right template for FCC.
- **Text2CAD** (NeurIPS 2024 spotlight, **CC BY-NC-SA 4.0**, <https://arxiv.org/abs/2409.17106>): text →
  DeepCAD sequence. **Arc F1 only 36.03** (i.e. every fillet and radius), median CD 0.37 but mean CD 26.41 —
  a long tail of blow-ups. Its "expert" prompts literally recite coordinates, so the model is a code
  formatter. Adds nothing over a general LLM once you have a good description. **Skip.**
- **CADFusion** (ICML 2025, code+weights **MIT**, <https://github.com/microsoft/CADFusion>): alternates SFT
  with **visual feedback** (render the sequence, DPO on renders). The **render-and-critique loop is the
  highest-value transferable idea** and needs zero third-party weights.
- Research-only or unusable: CAD-MLLM (no inference code, no LICENSE), CADCrafter (code not released),
  SolidGen (repo 404), BrepGen (non-commercial/GPLv3-for-research, B-REP), HNC-CAD (non-commercial),
  SkexGen (**MIT**, unconditional only), Point2Cyl (**MIT**, ~10× worse CD than CAD-Recode), ComplexGen
  (**MIT**, point cloud → B-REP), SECAD-Net (**MIT**, implicit sketches needing re-fitting).
- **"CAD-LLM"** did not resolve to a first-party paper or repo — **unverified; treat claims about it as unsourced.**

**Zoo / KittyCAD Text-to-CAD** (<https://zoo.dev/docs/api/ml>): `POST /ai/text-to-cad/{output_format}` is
currently marked **deprecated and hidden**; returns `code` (a **KCL** program) plus STEP/mesh exports. The app
is **MIT** (<https://github.com/KittyCAD/modeling-app>) but **the models are closed and hosted**. Pricing:
"$10 of Free API calls per month, then **$0.0083 per second**" (<https://zoo.dev/api-pricing>). Violates
offline-first, sends the customer's drawing to a third party, and its input is *text* — it cannot read a
drawing. **Not a component.**

### 6. Photo-of-an-object → 3D: skip the whole family

Common contract, verified per model: input is one RGB image (background-removed, object-centred, ~512²);
output is a mesh / 3D Gaussians / implicit field **in a normalised, unitless frame**. No camera intrinsics, no
metric anchor — **scale is ambiguous by construction**, and metrics are Chamfer/F-score/CLIP *after alignment
and rescaling* (TripoSR <https://arxiv.org/abs/2403.02151>; TRELLIS <https://arxiv.org/abs/2412.01506>).
Licences are a minefield: **MIT** for TripoSR, TRELLIS, PartCrafter; **CC-BY-NC** weights for Zero123++ and
OpenLRM (and therefore for InstantMesh's pipeline despite its Apache code); Stability Community (non-commercial
above $1M) for Stable Fast 3D; the Tencent Community licence for Hunyuan3D **expressly excludes the EU, UK and
South Korea**; S-Lab NC for MeshAnything V1. On Apple Silicon only SF3D documents first-party MPS support
("experimental") and Hunyuan3D-2 claims macOS; TRELLIS's README says "tested only on Linux" with NVIDIA ≥16 GB
and flash-attn. **The representation is wrong (no sketch/extrude/hole/datum) and the numbers never enter the
model. Skip.**

---

## What this would mean for FreeCadCopilot

### Proposed pipeline (deterministic first, VLM as tie-breaker)

The architecture the literature converges on — arXiv 2602.18296's "deterministic-first, escalate to the LLM
only on ambiguity", Img2CAD's factorisation (VLM picks features, a fitter picks sizes), and CADFusion's
render-and-critique — is the same shape. Concretely, in order of value:

1. **Vector cross-check of every number (do this first).** `get_drawings` → filter dimension-coloured paths →
   split strokes from small filled arrowhead paths by `type` + `rect` → arrowhead `rect` centres give the
   measured span in points → ÷1.7573 pt/mm → compare against the text-layer numeral. Flag any mismatch beyond
   drafting slop. **Kills the "108 read as 100" class deterministically. Zero new dependencies.**
2. **Vector-signature ⌀ / R / ° detection** on the same `get_drawings` output (§4.2 item 1). ~50 lines,
   no dependency. This closes the known gap where `⌀35 THRU` arrives from the text layer as `35 THRU`.
3. **Dimension-set grouping in the classical sense** (§1): group {text bbox, dimension line, two arrowheads,
   two extension lines} in PDF user space, then attach each group to the contour edge its extension lines
   touch. This is the *association* half, and it is plain 2D geometry because both sources share one
   coordinate system.
4. **Crops sized by the provider's own arithmetic** (§4.5): keep each crop's long edge ≤1568 px (≤2576 px on
   Claude 4.7+), ≤20 images per request, `detail:"original"` on OpenAI, prompt-before-image on Gemini. Never
   send a full ~3070 px sheet.
5. **Apple Vision as an independent second reader** for the raster-only worst case (`.accurate`,
   `usesLanguageCorrection = false`, `minimumTextHeight` lowered or per-view crops, consume `topCandidates(3)`
   as a confusion set). MIT wrapper, no model download, no CUDA, macOS-native.
6. **Shape from the 186 DPI raster, topology only**: `VNDetectContoursRequest` with `maximumImageDimension`
   raised to the real crop size (hole hierarchy for free), or autotrace `-centerline` / OpenCV LSD +
   `HoughCircles` + `fitEllipse`. Then **snap** every fitted length and radius to the values already known
   exactly from the dimension apparatus. **Never let traced pixels set a dimension (±0.44 mm floor).**
7. **Render-and-critique** (CADFusion / cadrille's IoU reward, adapted): project the candidate solid with
   FreeCAD TechDraw, diff against the traced contour, iterate. Offline, no third-party weights.
8. **Optional, later:** `cadrille` on a per-view crop as a *shape/topology proposer only* — then re-bind every
   numeric literal from the exact PDF text. Note the CC-BY-NC weights before shipping.

### Rejected options, and why

| Rejected | Why |
|---|---|
| Photo→3D generators (TripoSR, TRELLIS, PartCrafter, InstantMesh, Hunyuan3D, SF3D, MeshAnything) | Unitless mesh output, scale ambiguous by construction, wrong representation, mostly Linux+CUDA, several non-commercial or geo-restricted licences |
| Any generative model as a **dimension source** | All emit normalised 6–8-bit-quantised coordinates (~1 mm on a 200 mm part). We already have exact numerals — letting a model re-predict them is a regression |
| Text2CAD | Arc F1 36.03; its "expert" prompts recite coordinates, so it is a code formatter. A general LLM already does this given a good description. CC-BY-NC-SA |
| Zoo/KittyCAD Text-to-CAD | Hosted-only (endpoint deprecated), sends the drawing off-machine, input is text — it cannot read a drawing |
| Drawing2CAD / Ortho2CAD as shipped components | 20% invalidity on toy parts; no weights (Ortho2CAD); no dimensions in the input (Drawing2CAD). Read them, do not depend on them |
| Mesh → B-rep as a route to a parametric part | `makeShapeFromMesh` produces one planar face per triangle; ReverseEngineering WB is manual per-primitive fitting. A mesh never becomes parametric automatically |
| potrace as the default tracer | Outline-only: a drawn edge returns as a 2 px-wide closed loop, not a line. Prefer autotrace `-centerline` or Vision contours |
| Higher render DPI | [measured] the views are embedded PNGs at 186 DPI — 186 DPI is a hard ceiling and re-rendering cannot recover geometry |
| Architectural symbol-spotting models (FloorPlanCAD/CADTransformer/SymPoint) | Trained on floor plans with labelled DWG/SVG symbol classes; different domain, and we have no mechanical training data |

### Not covered — grandchild did not return

The sub-agent assigned to **classic orthographic-view reconstruction and the vector-drawing symbol-spotting
cluster** died on repeated provider connection errors and produced only a skeleton. The following are therefore
**not verified in this document** and should be treated as open: the bottom-up 3-view reconstruction line
(Idesawa; Wesley & Markowsky; Sakurai & Gossard; Meeran & Pratt; Company/Contero surveys) and its modern code
status; FloorPlanCAD / GAT-CADNet / ArchCAD-400K dataset licences and domains; PlanE; engineering-drawing
digitization surveys as a body; and title-block / table-parsing specifics.

## Open questions

1. Is the dimension-line colour convention ([measured] green strokes, blue section leaders) stable across
   sheets from other CAD systems, or is it a SolidWorks-export artefact? The whole §4.1 cross-check keys on it —
   what is the fallback discriminator (line width, dash pattern, arrowhead adjacency) when colour is not there?
2. What is the drafting-slop threshold above which a geometry↔numeral mismatch should be recorded as a
   `conflict` rather than silently accepted? (Broken/foreshortened dimension lines will legitimately disagree.)
3. Does the arrowhead survive as a *filled* path in exports from other CAD systems, or as a stroked polyline
   (which would land in pdfplumber's `.curves`)?
4. For raster-only PNG input there is no vector apparatus at all — is the plan there Vision OCR + traced
   contours + explicit low confidence, or do we decline to read such sheets?
5. Would a small fine-tune (the Florence-2 result, <https://arxiv.org/abs/2411.03707>) be worth it later, and
   where would the annotated mechanical drawings come from — Ortho2CAD's pythonOCC generator over STEP files?
6. Does Apple Vision tolerate small skews in dimension text, or must every rotated callout be de-rotated using
   the PDF text CTM first? (Undocumented; needs measurement.)
7. Licence question to settle before distribution: PyMuPDF is AGPL-3.0-or-commercial and the workbench links it.
