# Evidence: analogs and related work

Compiled from this repo's own research literature review (`docs/research/*.md`) and the
project README. Every claim below is sourced to a specific doc; anything not directly
supported by those docs is marked **unverified**. This is evidence for a practice report,
not a fresh literature search — no claim here goes beyond what the repo's research already
checked.

## 1. Comparison table

Columns: **Param** = parametric, editable output (not a dead mesh/B-rep). **Drawing-in** =
takes a dimensioned engineering drawing as input (not text/photo/point-cloud). **In-host** =
runs inside a CAD application's own document model. **Deterministic** = the pipeline has a
fixed, non-LLM-routed control flow. **Measurable** = the project publishes or computes a
pass/fail acceptance metric. **Cost/license** = what it takes to actually run it.

| Analog | Param | Drawing-in | In-host | Deterministic | Measurable | Cost / license |
|---|---|---|---|---|---|---|
| **MEDA** (ASME IDETC-CIE 2025) | ⚠️ not stated in source | ⚠️ not stated in source | ⚠️ not stated in source | **−** (LLM-routed 7-agent group chat) | ⚠️ not stated in source | ⚠️ not stated in source |
| **Zoo / KittyCAD Text-to-CAD** | **+** (KCL code, units first-class) | **−** (text only) | **−** (geometry engine in the cloud; endpoint now deprecated/hidden) | ⚠️ unverified (hosted LLM) | **−** (metrics not published) | tooling MIT; API $0.0083/s, ToS forbids benchmarking and competing development |
| **CAD-Assistant** (ICCV 2025) | **+/−** (writes real FreeCAD-API Python; PF1 0.979 but **CF1 only 0.484** — half the constraints are wrong) | ⚠️ not stated in source | **+** (native FreeCAD API, `.FCStd`) | **−** (LLM writes code per call; a Constraint Checker gives feedback, not a fixed pipeline) | **+** (published PF1/CF1) | CC BY-NC, requires an OpenAI API key |
| **Drawing→program family** (Drawing2CAD ACM MM'25, Ortho2CAD CMU 2026) | **+/−** (CadQuery/DeepCAD sequence, but normalized+quantized: 256 levels ≈ 1 mm on a 200 mm part — cannot distinguish 108 from 100 by construction) | **+/−** (Drawing2CAD: SVG, **no dimensions**; Ortho2CAD: raster ortho **with dimensions**, no weights released) | **−** (outputs CadQuery, not a native feature tree) | **−** (ML inference) | **+** (ACC_param 76.1 / IR 20.3 %; IoU 0.792) | Drawing2CAD MIT; Ortho2CAD code public but **no LICENSE file** = all rights reserved, no weights |
| **Photo→3D generative mesh family** (TripoSR, TRELLIS, Hunyuan3D, Tripo AI, Rodin) | **−** (mesh/gaussian/implicit field, unitless; no sketch/extrude/hole entities) | **−** (single photo, not a drawing) | **−** (import only; FreeCAD `makeShapeFromMesh` gives one planar face per triangle — measured in FreeCAD 1.1.3) | **−** | **+/−** (OmniMech: volume error 109–5 515 % on mechanical parts — measured, but not acceptance-style) | license minefield: Hunyuan3D excludes EU/UK/KR, Wonder3D weights AGPL; TripoSR is the one clean MIT/MIT candidate |
| **Mesh→B-rep import** (Fusion 360 Convert Mesh, OCCT Sewing, FreeCAD ReverseEngineering) | **−** (Fusion "Parametric" = one timeline node, not dimensioned sketches; FreeCAD fits single primitives — Cylinder/Cone **segfault**, Sphere silently wrong, only Plane works — measured) | **−** (mesh input, not a drawing) | **+** (native to each host) | **+** (geometric algorithm, not ML — but brittle) | **+/−** (measured fit residual Ø19.97 vs true 20, i.e. 0.13 %, on the one primitive that works) | Fusion 360 Organic extension is paid; FreeCAD path is LGPL/offline |
| **This project (FreeCAD Copilot)** | **+** (live `PartDesign` feature tree via the FreeCAD Python API; STEP re-import degrades to a single `Part::Feature` — measured, so STEP is rejected as an output format) | **+** (vector text layer + arithmetic cross-check from dimension-line spans; catches a mis-read 108→100 by measuring 107.18 mm from the drawing itself) | **+** (lives inside FreeCAD's own UI) | **+** (fixed Design→Plan→Execute→Assertion→Review order, ADR-0003/0018/0019/0021; LLM never chooses the route) | **+** (Assertion phase checks the built part against an Acceptance list; Preflight/DoF/ConflictingConstraints checks) | LGPL/offline-first, no cloud geometry engine dependency |

## 2. Per-analog notes

**MEDA** (ASME IDETC-CIE 2025) — the project's own explicitly named departure point
(top-level `README.md`): "routes seven agents through a group chat." The repo's docs give no
further detail on MEDA's input, output format, or metrics beyond this one architectural fact,
so most of its table cells above are marked unverified rather than guessed. It is cited
purely as the LLM-routed alternative this project deliberately does not build.

**Zoo / KittyCAD Text-to-CAD** — genuinely parametric (KCL, first-class units), but it is a
hosted service with the geometry kernel in the cloud, the public endpoint is now deprecated,
its input is text (it cannot read a drawing at all), and its ToS forbids both benchmarking
and building a competing product (`drawing-understanding-literature.md`,
`image-to-parametric-cad.md`).

**CAD-Assistant** (ICCV 2025, arXiv:2412.13810) — architecturally the closest published system
to this project: an LLM planner that writes and runs Python against a real CAD API. Its
Autoconstraining Feature-completeness score (PF1 0.979) is strong, but its Constraint-type
score (CF1 0.484) shows it gets barely half of individual constraint types right, and PF1 falls
to 0.747 without its Constraint Checker verify loop (`README.md` §8.2,
`raster-to-vector-tooling.md`). CC BY-NC, needs an OpenAI key — not shippable as-is.

**Drawing→program family (Drawing2CAD, Ortho2CAD)** — Drawing2CAD (ACM MM 2025) is the
best-scoring published drawing-to-CAD system, but its SVG input carries **no dimensions at
all**, and every model in this family normalizes into a fixed unit cube quantized to a fixed
grid, which mechanically prevents distinguishing 108 mm from 100 mm on a 200 mm part
(`drawing-understanding-literature.md`, `image-to-parametric-cad.md`). Ortho2CAD (CMU, 2026)
is the one paper whose raster input actually carries dimensions and reaches 100 % syntactically
valid code, but ships no weights and no LICENSE file.

**Photo→3D generative mesh family** (TripoSR, TRELLIS, Hunyuan3D, Tripo AI, Rodin) — dozens of
models exist; none produces a parametric result, and the repo's research treats this as a
structural fact, not an immaturity: every one normalizes into an arbitrary unitless box before
inference, so metric millimetres are discarded before the network ever runs
(`image-to-3d-generative-landscape.md`). OmniMech (arXiv:2608.05539) is the one benchmark that
measures this on real mechanical parts and finds volume errors from 109 % to 5 515 %.

**Mesh→B-rep import** (Fusion 360 Convert Mesh, OCCT Sewing/UnifySameDomain, FreeCAD
ReverseEngineering) — the traditional, deterministic route from a mesh to a solid. It is native
to the CAD host and geometrically exact where it works, but it does not recover dimensioned
sketches: Fusion's own docs describe "Parametric" as a single timeline node, and FreeCAD's
primitive-fit tooling is measured to segfault on Cylinder/Cone and silently misfit Sphere,
leaving only Plane reliable (`mesh-to-parametric-bridge.md`, measured in FreeCAD 1.1.3).

**This project** — combines a deterministic, arithmetic-checked drawing reader (vector text
layer plus dimension-line span cross-check, catching the ADR-0019 108→100 case without a model
in the loop) with output written directly through the FreeCAD Python API into a live
`PartDesign` feature tree, inside a fixed non-LLM-routed pipeline with explicit acceptance
checks (`README.md`, top level; `docs/research/README.md`).

## 3. The unsolved problem this project addresses

None of the analogs above combine all three of: (a) a **metrically exact** drawing reader — every
published drawing/photo/point-cloud → CAD system quantizes or normalizes away the millimetre
before a number ever reaches the model, so none can distinguish 108 mm from 100 mm by
construction (`drawing-understanding-literature.md`, `image-to-parametric-cad.md`); (b) output
as a **native, editable feature tree** rather than a mesh, a B-rep with no history, or code in a
foreign DSL (CadQuery/KCL) that degrades on STEP re-import to a single opaque
`Part::Feature` (measured); and (c) a **deterministic, exit-conditioned pipeline** instead of
LLM-chosen agent routing (the explicit departure from MEDA), so a run has a checkable Assertion
against an Acceptance list rather than an open-ended chat that may or may not converge. This
project's structural bet — read the drawing's own vector layer and cross-check it arithmetically,
then write directly into FreeCAD's native parametric model under a fixed pipeline — is not
attempted as a combination in any analog surveyed in the repo's research.

## 4. Academic works cited in `docs/research`, with year and what is used

| Work | Year / venue | What is used from it |
|---|---|---|
| DeepCAD (arXiv:2105.09492) | 2021 | Quantization ceiling evidence: 2×2×2 cube, 256 levels ≈ 1.04 mm on a 200 mm part — the reason generative CAD cannot resolve 108 vs 100. |
| Fusion 360 Gallery dataset | 2021, Autodesk (non-commercial) | Cited as the only corpus with real design-intent operation ordering; explicitly **not reused** because its license binds the user's employer. |
| SketchGraphs (github.com/PrincetonLIPS/SketchGraphs) | 2020 | Reused directly: its 10 constraint types cover ~85 % of usage, mapped onto `Sketcher::ConstraintType` for the autoconstrain baseline. |
| ABC dataset | 2019 | Named as the largest B-rep corpus; used only as background, not reused directly. |
| Vitruvion (arXiv:2109.14124, ICLR 2022) | 2022 | Conceptual template: trace → primitives → constraint graph → bind the dimension to the constraint; PF1 0.706 baseline cited against CAD-Assistant's 0.979. |
| BRepNet (arXiv:2104.00706) | 2021 | Source of the "designer may choose cylinder-via-circle or revolved-rectangle" argument that feature recognition is underdetermined; its per-face segmentation numbers (acc 92.5, IoU 77.1) used as a feature-recognition ceiling. Rejected for reuse (CC BY-NC-SA). |
| Drawing2CAD (arXiv:2508.18733, ACM MM 2025) | 2025 | Raster-vs-vector ablation (75.14 % vs 70.49 % param-accuracy) used as the published argument for vectorizing the drawing before showing it to a model. |
| Ortho2CAD (arXiv:2607.08891, CMU) | 2026 | Cited as the only paper whose input carries real dimensions; its synthetic dimensioned-drawing generator noted as worth reusing to build eval data; not reused directly (no weights, no LICENSE). |
| CAD-Recode (arXiv:2412.14042, ICCV 2025) | 2025 | Quantization evidence (unit box, step 1 = 1 mm at 200 mm scale); its own stated limitation (no revolve/fillet) cited to bound project scope expectations. |
| cadrille (arXiv:2505.22914, ICLR 2026) | 2026 | RL verify-loop evidence reused as a template: reward `10·IoU − 10·[invalid]` cut Invalid Ratio from 5.9 % to 0.2 %. |
| CADCodeVerify | cited, undated in source | Evidence that solver feedback (0.102) beats visual/render feedback (0.132) — used to justify verifying through the geometric kernel, not screenshots. |
| Text2CAD (arXiv:2409.17106, NeurIPS 2024) | 2024 | Cited and explicitly rejected: Arc F1 only 36.03, adds nothing over a general LLM once a good text description exists. |
| CADFusion (github.com/microsoft/CADFusion, ICML 2025) | 2025 | Render-and-critique training-loop idea noted as the highest-value transferable concept; not reused directly (training loop, not inference). |
| CAD-Assistant (arXiv:2412.13810, ICCV 2025) | 2025 | Architecturally closest published analog (LLM planner over a CAD Python API); its PF1/CF1 split used as the benchmark for how much of "correct constraints" is still unsolved; its finding that geometry serialization format changes accuracy is cited as directly actionable. |
| CADFit (arXiv:2605.01171) | 2026 | Cited as the one metric method that fits a CadQuery program in the mesh's own coordinates without normalization/quantization (IoU 0.960); not reused (235–904 s/part, CC BY-NC). |
| Zero-to-CAD (arXiv:2604.24479, Autodesk) | 2026 | Cited for Apache-2.0 weights and a richer operation vocabulary beyond sketch-and-extrude, produced via an LLM agent with environment feedback. |
| Img2CAD (arXiv:2408.01437 and arXiv:2410.03417) | 2024 | The factorization idea — a VLM picks which features exist, a deterministic fitter picks the numbers — reused as the project's own architectural template. |
| MechVQA | cited, no arXiv id resolved in source | Benchmark evidence for the core failure mode: 78.65–87.74 % accuracy on dimension/annotation reading — used to justify never asking the model for a number the text layer already has. |
| AECV-Bench (arXiv:2601.04819) | 2026 | Benchmark evidence: up to 0.95 accuracy on OCR/text QA, only 0.40–0.55 on symbol-centric drawing understanding, called "unsolved" — supports the OCR-good/geometry-bad split the project's architecture is built on. |
| DesignQA (arXiv:2404.07917) | 2024 | Cited: frontier MLLMs "encounter difficulty in analyzing engineering drawings" — corroborates AECV-Bench on a different benchmark. |
| TripoSR (arXiv:2403.02151) | 2024 | Normalization evidence (cube [−0.5,0.5]³); the one photo→3D candidate with fully clean MIT/MIT licensing, kept as an optional reference-mesh proxy, never a dimension source. |
| TRELLIS (arXiv:2412.01506) | 2024 | Further normalization evidence (`clip(-0.5,0.5)` at `voxel_size=1/64`) for the "no generative model carries millimetres" claim. |
| OmniMech (arXiv:2608.05539) | 2026 | The only benchmark measuring photo/drawing→3D generative models specifically on mechanical parts; volume error 109–5 515 % used as the strongest evidence against the whole generative-mesh family for this domain. |

## Flags — could not verify from the repo's docs

- MEDA's input/output format, whether it targets a CAD host, and any published metric: not
  described anywhere in `docs/research/*.md` or the top-level `README.md` beyond "routes seven
  agents through a group chat." Any further detail would need MEDA's own paper, which is
  outside this repo's research corpus.
- CAD-Assistant's exact input modality (whether it accepts a dimensioned drawing at all, or
  only a design intent/text prompt) is not stated in the source docs beyond the PF1/CF1
  constraint-authoring numbers.
- CADCodeVerify's publication year/venue is not given in the source doc that cites it.
- MechVQA's arXiv identifier is never given in the source docs; only its reported accuracy
  numbers are cited (repeated consistently across three separate docs: 78.65 %, 84.99 %,
  87.74 %; and 75–88 % / 79–88 % restated as a range elsewhere).
