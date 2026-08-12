# Reading a technical drawing with a VLM: what actually works, and how the numbers are made reliable

**Slug:** `vlm-drawing-reading-practice`
**Scope:** first-party provider specs, dedicated OCR/document-AI as ground truth, SVG-as-text, and
cross-checking patterns — measured against what `core/sheet_reader.py` already does.

## Question

What actually works when feeding a technical drawing to a VLM/LLM, and how do people make the
numbers reliable?

## Ground truth this report is written against

Measured on this repo's own `tests/modelmania/*.pdf` (all four sheets, same structure — supplied to
this research, not re-derived here):

- Every **view** (the part geometry) is an **embedded PNG raster placed at exactly 186 DPI**
  (`mm25-p1-1.pdf`: 5 images, 616×245 … 712×585 px). Re-rendering the PDF at a higher DPI cannot
  recover geometry. **186 DPI is a hard ceiling** on shape.
- The **dimension apparatus is vector**: dimension/extension lines are real PDF path items
  (green stroke `rgb(0, 0.5, 0)`), section arrows/leaders are blue paths (585 `l` items in p1-1).
- The **text is vector with exact bounding boxes** (`page.get_text("words")`): `40`, `(R30)`,
  `35 THRU`. `Ø` is *not* in the text layer — it is drawn as geometry.
- **Scale is exactly recoverable**: green dimension path widths 263.6 pt ↔ `150` and 87.9 pt ↔ `50`
  both give 1.7573 pt/mm (0.569 mm/pt) → **4.54 px/mm at 186 DPI**, i.e. 1 raster pixel ≈ 0.22 mm.

This inverts the framing of ADR-0019. "108 read as 100" is an 8 mm error. The numerals are already
exact, in the text layer, with coordinates — so it is **not an OCR problem**. What is unsolved is
**(a) association** (which number belongs to which edge) and **(b) shape** (the outline exists only
as 186 DPI pixels, recoverable to ≈ ±0.25 mm — 30× better than the failure being chased).

## Executive summary

1. **`MAX_EDGE = 1400` in `core/sheet_reader.py` is wrong and is silently costing 22% of the
   resolution the module exists to protect.** Anthropic's binding constraint is the *visual-token*
   budget, not the edge: cost is `⌈w/28⌉ × ⌈h/28⌉` tokens, capped at 1568 tokens on the standard
   tier. A 1400×1400 crop is 2500 tokens, so it is downscaled to **1092×1092**. The largest square
   that survives untouched on the standard tier is **1092 px**, not 1400.
   ([vision](https://platform.claude.com/docs/en/build-with-claude/vision),
   [coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))
2. **A high-resolution tier now exists and the repo is not using it.** Claude 4.7 and later:
   max long edge **2576 px**, **4784** visual tokens. The largest untouched square becomes
   **1932 px**, and a 1400×1400 crop passes through unresized. This is a one-constant change worth
   ~1.77× linear resolution on every crop.
   ([vision](https://platform.claude.com/docs/en/build-with-claude/vision))
3. **But for this repo it barely matters, because the provider is no longer the bottleneck — the
   186 DPI embedded raster is.** A 712×585 view crop costs 546 tokens and is *never* resized on
   either tier. Upscaling the crop past its native 186 DPI adds tokens and no information. The
   correct rule is: crop to the view, render at the raster's own 186 DPI, stop.
4. **Every provider's own docs disclaim exactly the things a drawing is made of.** OpenAI:
   *"The model may misinterpret rotated or upside-down text"* and *"may struggle to understand
   graphs or text where colors or styles — like solid, dashed, or dotted lines — vary"* — that is
   centrelines and hidden lines, named.
   ([OpenAI vision limitations](https://platform.openai.com/docs/guides/images-vision))
   Anthropic: *"Claude might hallucinate or make mistakes when interpreting low-quality, rotated, or
   very small images"*, and *"coordinate and localization outputs are approximate"*.
   ([vision](https://platform.claude.com/docs/en/build-with-claude/vision))
5. **Never send this PDF as a PDF.** Anthropic rasterises PDF pages server-side *"at dimensions you
   don't control"*, and states plainly that returned coordinates *"can't be reliably mapped back
   onto the page"* — rasterise yourself.
   ([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))
   Gemini scales pages to at most 3072×3072 and bills 258 tokens/page.
   ([document processing](https://ai.google.dev/gemini-api/docs/document-processing))
   `sheet_reader.py` already rasterises; this is the first-party justification for keeping it.
6. **Grounding is asymmetric across providers, and it decides the design.** Gemini is *trained* to
   emit bounding boxes, normalised `[ymin, xmin, ymax, xmax]` to `[0, 1000]`
   ([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)). Anthropic's
   stance is the opposite: *"Claude works best with absolute pixel coordinates. Ask for them
   explicitly… Claude does not work well when you ask for normalized coordinates."*
   ([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))
   Getting this backwards silently shifts every box.
7. **Anthropic's Citations API is the "point at the callout you used" mechanism, enforced by the
   API rather than by prompt.** A *custom content document* takes your own list of chunks with no
   further chunking, and the response returns `cited_text` plus the exact chunk index — and
   `cited_text` does not count against output tokens.
   ([citations](https://platform.claude.com/docs/en/build-with-claude/citations))
   Feed `printed_on_the_sheet` as the chunk list and a reading that cites nothing is rejectable
   mechanically. Caveat: *"Only text citations are currently supported. Image citations are not yet
   possible"* — so the model cites the extracted string, not the pixels.
8. **Structured output is now constrained decoding on all three, so schema violations stop being a
   failure mode.** Anthropic `output_config.format` with `json_schema`
   ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs));
   Gemini `response_format` with a JSON schema, used in Google's *own* bounding-box example
   ([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)).
   This is free reliability for `design.py`'s conflict/`not_defined` structure.
9. **The literature has converged on one shape, and it is not "send the sheet to a VLM".** It is
   *detect first, read second*: rotation-aware oriented-bounding-box detection of each annotation,
   then a small VLM on each cropped patch. Two independent 2025 papers do exactly this with
   YOLOv11-obb + Donut/Florence-2 ([arXiv:2506.17374](https://arxiv.org/abs/2506.17374),
   [arXiv:2510.21862](https://arxiv.org/abs/2510.21862)); a third fine-tunes a 0.23 B Florence-2 and
   beats zero-shot GPT-4o and Claude 3.5 Sonnet on GD&T extraction by +52% F1 and −43% hallucination
   ([arXiv:2411.03707](https://arxiv.org/abs/2411.03707)). **This repo already has the detector for
   free** — the OBBs are in the PDF vector layer.
10. **The benchmark evidence says the split is precisely OCR-good / geometry-bad.** AECV-Bench
    (2026) reports OCR and text-centric document QA up to **0.95** accuracy while symbol-centric
    drawing understanding and counting stay at **0.40–0.55** and are called *"unsolved"*
    ([arXiv:2601.04819](https://arxiv.org/abs/2601.04819)). DesignQA finds frontier MLLMs
    *"encounter difficulty in analyzing engineering drawings"*
    ([arXiv:2404.07917](https://arxiv.org/abs/2404.07917)). Do not ask a VLM for the number. Ask it
    for the relation, give it the number.

## Findings

### 1. Anthropic Claude — image input, the real cap, and coordinates

**Patches, not pixels.** *"Claude views images in patches instead of pixels. Each patch is a 28×28-pixel
block… An image, therefore, costs `⌈width / 28⌉ × ⌈height / 28⌉` visual tokens."*
([vision](https://platform.claude.com/docs/en/build-with-claude/vision))

| Tier | Models | Max long edge | Max visual tokens | Largest untouched square |
|---|---|---|---|---|
| High-resolution | Claude 4.7 and later | 2576 px | 4784 | **1932 px** (4761 tok) |
| Standard | all other models | 1568 px | 1568 | **1092 px** (1521 tok) |

Source for the first four columns: [vision](https://platform.claude.com/docs/en/build-with-claude/vision).
The fifth column is computed from Anthropic's own published `resized_size` reference implementation
([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)); the docs
give the same shape of result for the non-square cases they tabulate (1920×1080 → 1456×819;
2000×1500 → 1269×952).

**The token limit, not the edge limit, is what usually resizes.** Anthropic says so explicitly, and
warns that assuming otherwise is *"the most common cause of misaligned coordinates"*: *"an A4 page
scanned at 130 DPI is 1075×1520 pixels: both sides are under 1568 px, but it costs 39 × 55 = 2145
visual tokens, so Claude resizes it to 924×1307."*
([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))
High-resolution-tier models do **not** resize that scan — 2145 is inside 4784.

Applied to this repo, using Anthropic's own algorithm:

| Crop | Standard tier | High-res tier |
|---|---|---|
| 1400×1400 (`MAX_EDGE`, square) | **downscaled to 1092×1092**, 1521 tok | 1400×1400 unchanged, 2500 tok |
| 1400×1050 | downscaled to 1270×952, 1564 tok | 1400×1050 unchanged, 1900 tok |
| 712×585 (a real 186 DPI view) | unchanged, 546 tok | unchanged, 546 tok |
| 616×245 (a real 186 DPI view) | unchanged, 198 tok | unchanged, 198 tok |

**Padding.** *"Claude then pads every image… up to the next multiple of 28 pixels on the bottom and
right edges… Always normalize or rescale by the resized dimensions, not the padded dimensions."*
([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))

**Other hard limits.** 8000×8000 px max dimensions; 10 MB per image on the Claude API (5 MB on
Bedrock/Vertex); 100 images per request on 200k-context models, 600 otherwise, but a 32 MB request
cap usually bites first. **A trap for a crop-heavy pipeline:** *"If a single API request contains
more than 20 images, a stricter per-image dimension limit applies… resize each image so that neither
dimension exceeds 2000 px, or keep the request to 20 or fewer image and document blocks."*
`sheet_reader.py` emits one overview plus a crop per region per page — the 20-block threshold is
reachable on a multi-page set. ([vision](https://platform.claude.com/docs/en/build-with-claude/vision))

**Documented quality guidance** (all from the same page): images before text; *"if the image contains
important text, make sure it's legible and not too small"*; *"avoid cropping out key visual context
solely to enlarge the text"*; and on compression, *"heavy JPEG compression can make text difficult to
read"* — render crops as PNG, not JPEG.

**Documented limits that bear on drawings:** *"Claude might hallucinate or make mistakes when
interpreting low-quality, rotated, or very small images under 200 pixels"*; *"Claude's coordinate and
localization outputs are approximate"*; *"Claude can give approximate counts of objects in an image
but might not always be precisely accurate, especially with large numbers of small objects."*
There is **no published OCR accuracy number** anywhere in Anthropic's vision or PDF docs.

**Coordinates / grounding — Anthropic's stance.** Claude *can* return boxes but wants them in absolute
pixels: *"Claude works best with absolute pixel coordinates. Ask for them explicitly… Claude does not
work well when you ask for normalized coordinates, for example: 'Return bounding box coordinates
between 0 and 1000.' Always ask for pixel coordinates and normalize in your own code."* Origin is
top-left. And for fine targets the doc's own advice is exactly this repo's strategy: *"Small elements
lose precision when an image is downscaled: for fine targets, crop the region of interest and send
the crop (offset returned coordinates by the crop origin), or use a high-resolution-tier model."*
([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))

**PDF handling.** *"Because PDF support relies on Claude's vision capabilities, it is subject to the
same limitations"*; 32 MB request, 600 pages (100 under 1M context); *"each page is processed as an
image"*; *"dense PDFs (many small-font pages, complex tables, or heavy graphics) can fill the context
window before reaching the page limit."*
([PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support))
Decisive for coordinates: *"For PDF support, pages are rasterized to images server-side at dimensions
you don't control, so the returned coordinates can't be reliably mapped back onto the page. To work
with coordinates on PDF content, rasterize the pages to images yourself."*
([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates))
A Bedrock-specific trap: on the Converse API, visual PDF understanding requires citations enabled,
otherwise *"the API falls back to basic text extraction only"* (~1 000 tokens for a 3-page PDF versus
~7 000 with full visual understanding).
([PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support))

**Citations as enforced grounding.** Custom content documents *"give you control over citation
granularity. No additional chunking is done and chunks are provided to the model according to the
content blocks provided."* Responses carry `cited_text`, `document_index` and char offsets. Anthropic
claims over prompt-based citing: *"cost savings because `cited_text` does not count toward your output
tokens"*, *"citations are guaranteed to contain valid pointers to the provided documents"*, and *"in
Anthropic's evaluations, the citations feature is significantly more likely to cite the most relevant
quotes."* Limit: *"Only text citations are currently supported. Image citations are not yet
possible"*, and citations must be enabled on all documents in a request or none.
([citations](https://platform.claude.com/docs/en/build-with-claude/citations))

**Structured outputs.** `output_config.format` with `type: "json_schema"` gives *"schema-compliant
responses through constrained decoding"*; `strict: true` does the same for tool inputs. The docs name
*"extract data from images or text"* as a target use case. Out of beta — no header required.
([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs))

### 2. OpenAI — patch budgets and an unusually candid limitations list

Newer models tokenise with **32×32 px patches** and a **patch budget**:
`original_patch_count = ceil(w/32) × ceil(h/32)`; if it exceeds the budget the image is scaled by
`shrink_factor = sqrt((32² × patch_budget) / (w × h))`, then adjusted down to integer pixel
dimensions. `detail` selects the budget: on gpt-5.5/5.4, `high` → **2500 patches or 2048 px max
dimension**, `original` → **10 000 patches or 6000 px max dimension**; smaller models (`*-mini`,
`*-nano`, `o4-mini`) get **1536 patches / 2048 px**. Small models then apply a token multiplier
(1.62 for mini, 2.46 for nano, 1.72 for o4-mini).
([images and vision](https://platform.openai.com/docs/guides/images-vision))

Older tile-based models (GPT-4o, GPT-4.1, o-series except o4-mini) instead: fit inside 2048×2048,
scale the **shortest side to 768 px**, count **512 px tiles**, add base tokens
(gpt-4o/4.1: 85 base + 170/tile; gpt-5: 70 + 140; o1/o3: 75 + 150).
([images and vision](https://platform.openai.com/docs/guides/images-vision))
`detail: "original"` on GPT-5.6 preserves input dimensions and *"does not resize the image to a
pixel-dimension or patch-budget limit"* — the one first-party way to guarantee no server-side
resampling of a drawing crop, at the cost of tokens.

OpenAI's limitations list names the drawing failure modes directly:
- *"**Small text**: Enlarge text within the image to improve readability. When available, using
  `"detail": "original"` can also help performance."*
- *"**Rotation**: The model may misinterpret rotated or upside-down text and images."*
- *"**Visual elements**: The model may struggle to understand graphs or text where colors or
  styles — like solid, dashed, or dotted lines — vary."*
- *"**Spatial reasoning**: The model struggles with tasks requiring precise spatial localization."*
- *"**Counting**: The model may give approximate counts."*

([images and vision](https://platform.openai.com/docs/guides/images-vision))
On a mechanical drawing that reads as: hidden lines, centrelines, phantom lines and section hatching
are *documented* weak spots, dimension text is rotated by convention, and the model is told not to be
trusted on localisation. No OCR accuracy figure is published.

### 3. Google Gemini — tiling, `media_resolution`, and first-class bounding boxes

**Tiling and token cost.** *"258 tokens if both dimensions ≤ 384 pixels. Larger images are tiled into
768×768 pixel tiles, each costing 258 tokens."* The published tile-count heuristic:
`crop_unit = floor(min(w, h) / 1.5)`, then divide each dimension by it — a 960×540 image gives a crop
unit of 360 and 3 × 2 = 6 tiles. Max **3600 image files per request**.
([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding))

**`media_resolution` (Gemini 3) is the knob for dense drawings**, and it is settable **per content
item**, so you can send one view at `high` and the sheet overview at `low` in the same request.
Published token counts per image: `low` 280, `medium` 560, `unspecified`/`high` 1120,
`ultra_high` 2240. Google's own guidance: images → `high` (1120); PDFs → `medium` (560), because
*"quality typically saturates at medium. Increasing to high rarely improves OCR results for standard
documents."* A drawing is not a standard document; treat that sentence as untested for line art.
([media resolution](https://ai.google.dev/gemini-api/docs/media-resolution))

**PDF handling.** Up to **50 MB or 1000 pages**; *"each document page is equivalent to 258 tokens"*;
*"larger pages are scaled down to a maximum resolution of 3072 × 3072 while preserving their original
aspect ratio, while smaller pages are scaled up to 768 × 768"*, and *"there is no… performance
improvement for pages at higher resolution."* On Gemini 3, native PDF text is extracted and passed to
the model, and **you are not charged for those native-text tokens**.
([document processing](https://ai.google.dev/gemini-api/docs/document-processing))
That last point is directly usable: a vector drawing PDF gets its text layer to the model for free.
But 3072 px across an 11 in sheet is 279 DPI on the *page*, and the geometry is a 186 DPI raster
inside it, so the sheet-level cap is not the limiting factor here either.

**Bounding boxes / grounding.** *"Models are trained to detect objects in an image and get their
bounding box coordinates. The coordinates, relative to image dimensions, scale to [0, 1000]. You need
to descale these coordinates based on your original image size."* Google's own example uses
`box_2d: [ymin, xmin, ymax, xmax]` **plus** structured output with a Pydantic JSON schema — i.e.
detection and schema-constrained extraction in one call. This is the strongest first-party grounding
primitive of the three providers, and note the axis order is **y-first**, the opposite of Claude's
`[x1, y1, x2, y2]` convention. ([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding))
**Regression to know about:** *"Image segmentation capabilities (returning pixel-level masks for
objects) are not supported in Gemini 3 Pro or Gemini 3 Flash… we recommend continuing to use Gemini
2.5 Flash with thinking turned off."* ([Gemini 3](https://ai.google.dev/gemini-api/docs/gemini-3))

**Documented best practices** (both the image and document pages): *"Verify that images are correctly
rotated"*, *"use clear, non-blurry images"*, and — the opposite of Anthropic's advice — *"when using a
single image with text, place the text prompt **before** the image"*
([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)) while the document
page says *"if using a single page, place the text prompt after the page"*
([document processing](https://ai.google.dev/gemini-api/docs/document-processing)).
Anthropic says *"Claude works best when images come before text"*
([vision](https://platform.claude.com/docs/en/build-with-claude/vision)). Ordering is
provider-specific and cannot be shared across a provider-agnostic prompt.

### 4. Dedicated OCR / document AI as ground truth

The honest headline for this repo first: **for a vector-text PDF, no OCR engine can beat
`page.get_text("words")`, because that is the draughtsman's own string with its own bbox and it
cannot be misread.** OCR only becomes relevant for genuinely raster attachments — and there it walks
straight into the two things every engine's own documentation disclaims.

#### 4.1 Rotation: every engine is out of contract on a drawing

**No engine in this survey documents free-angle (e.g. 37°) per-line text recognition.** The
commercial APIs document *whole-page* rotation; the open engines are 90°-quantised or ±180°.

| Engine | What the docs actually say | Source |
|---|---|---|
| **AWS Textract** | *"Horizontally arrayed text can be read regardless of the degree of rotation of a document. Amazon Textract does not support vertical text."* — i.e. all text must be **mutually parallel**. A sheet with dimension text at 0°, 90° and 37° at once is **explicitly outside the contract**. | [limits-document](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) |
| **Azure AI Document Intelligence** | `pages[].angle` is *"the overall text angle in degrees for pages that can be rotated"* — page-level. Words carry a 4-corner `polygon`, so a rotated quad *can be represented*, but nothing claims free-angle lines are detected. | [analyze-document-response](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept/analyze-document-response) |
| **Google Document AI** | Advertises *"even for rotated texts"*, but the machine-readable field is `Layout.orientation`, an enum of `PAGE_UP / PAGE_RIGHT / PAGE_DOWN / PAGE_LEFT` — **90° only**. | [enterprise-document-ocr](https://cloud.google.com/document-ai/docs/enterprise-document-ocr) |
| **Tesseract** | OSD is 90°-quantised; the docs instruct you to *"rotate the page image so that the text lines are horizontal"*. `psm 2` (auto page seg + OSD, no OCR) is marked *"(not implemented)"*. | [ImproveQuality](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html) |
| **PaddleOCR** | The textline orientation classifier has literally **two classes, 0° and 180°**; the page classifier does 0/90/180/270. | [PaddleOCR docs](https://paddlepaddle.github.io/PaddleOCR/) |
| **docTR** | **The only one** documenting genuinely rotated per-word polygons: `assume_straight_pages=False` will *"always fit and return rotated boxes"*, plus a crop-orientation classifier. | [docTR](https://github.com/mindee/doctr) |

This single table is why the 2025 literature does not use OCR line detection on drawings at all: it
puts a **rotation-aware oriented-bounding-box detector (YOLOv11-obb) in front** and only then reads
the de-rotated patch ([arXiv:2506.17374](https://arxiv.org/abs/2506.17374) — which states outright
that generic OCR fails on *"rotated text"*; [arXiv:2510.21862](https://arxiv.org/abs/2510.21862);
[arXiv:2505.01530](https://arxiv.org/abs/2505.01530), reporting **94.77% GD&T precision** with
YOLOv11-OBB → Donut over nine categories).

#### 4.2 Symbols: verified charsets, and they mostly do not contain the symbol

Checked against each project's published charset rather than its marketing page:

| Engine | `°` | `±` | `Ø` | `⌀` (U+2300) | `▽` / GD&T | Source |
|---|---|---|---|---|---|---|
| **AWS Textract** (published charset) | ✅ | ❌ | ✅ (Latin letter) | ❌ | ❌ | [limits-document](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html) |
| **Tesseract** `langdata/eng/desired_characters` | ✅ | ❌ | ❌ | ❌ | ❌ | [tesseract langdata](https://github.com/tesseract-ocr/langdata) |
| **docTR** `VOCABS["english"]` (digits + ascii + punct + `°` + currency) | ✅ | ❌ | ❌ | ❌ | ❌ | [doctr/datasets/vocabs.py](https://github.com/mindee/doctr/blob/main/doctr/datasets/vocabs.py) |
| **PaddleOCR PP-OCRv5** `ppocrv5_dict.txt` | ✅ | ✅ | ❌ | ✅ | ✅ (`▽ ⏥ ⊥ ∠ ⊕ ∅ Φ`) | [ppocrv5_dict.txt](https://github.com/PaddlePaddle/PaddleOCR) |
| **PaddleOCR PP-OCRv4** `en_dict.txt` (ASCII-only) | ❌ | ❌ | ❌ | ❌ | ❌ | same repo |
| **Azure DI / Google Doc AI** | *unpublished — do not assume* | | | | | — |

Two things follow. First, **PP-OCRv5 is the only engine whose charset can even emit `⌀`, `±` and
`▽`** — and the same project's previous version could not, so the version pin matters more than the
engine choice. Second, **no engine models a GD&T feature control frame at all**, which is consistent
with `sheet_reader.py`'s own measurement that `Ø` is absent from the text layer because it is drawn
as geometry. The published answer is object detection, not character recognition
([arXiv:2204.13277](https://arxiv.org/abs/2204.13277),
[arXiv:2411.03707](https://arxiv.org/abs/2411.03707)).

#### 4.3 Small text and DPI: documented floors

- **Azure DI**: *"the minimum height of the text to be extracted is 12 pixels for a 1024 × 768-pixel
  image… about 8-point text at 150 DPI"*; images 50×50 px min, 10 000×10 000 px max
  ([Read](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/read)).
- **Textract**: *"the minimum height for text to be detected is 15 pixels. At 150 DPI, this would be
  the same as 8 point font"*; *"provide a high quality image, ideally at least 150 DPI… don't convert
  or downsample the document before uploading"*; ≤10 000 px per side
  ([limits](https://docs.aws.amazon.com/textract/latest/dg/limits-document.html),
  [best practices](https://docs.aws.amazon.com/textract/latest/dg/textract-best-practices.html)).
- **Tesseract**: x-height band ~10–30 px, ≥300 DPI recommended
  ([ImproveQuality](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)).
- **Surya** recommends capping page width at 2048 px — which directly conflicts with the high DPI
  small text needs, making tiling mandatory ([Surya](https://github.com/datalab-to/surya)).

Applied here: at 186 DPI a 9.5 pt Century Gothic digit inks roughly 18 px — above Azure's 12 px and
Textract's 15 px floors, but below Tesseract's comfortable band, and with **no headroom**, because
186 DPI is the ceiling. **No vendor makes any statement about line art.**

#### 4.4 Confidence: the signal VLMs do not have

| Engine | Granularity | Notes |
|---|---|---|
| Google Document AI | per `Layout` unit, and **per symbol** if the symbol-level add-on is enabled | the only true per-character option ([enterprise-document-ocr](https://cloud.google.com/document-ai/docs/enterprise-document-ocr)) |
| AWS Textract | per WORD and LINE, 0–100 | *"the accuracy of the recognized text **and the accuracy of the geometry points**"* — conflates the two ([API_Block](https://docs.aws.amazon.com/textract/latest/dg/API_Block.html)) |
| Azure DI | **per word only**; lines have none | `{"content":"While","confidence":0.997}` ([layout](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout)) |
| Tesseract | per char internally; word conf is a min-over-chars heuristic | `tsv`/hOCR |
| PaddleOCR | **per line only** (`rec_scores`) | too coarse to flag one digit |
| docTR | per word | |
| Surya | per block = mean per-token probability | |
| olmOCR | **none** | |

**No vendor claims calibration.** But even an uncalibrated per-word score is more than any VLM
provider offers: none of Anthropic, OpenAI or Google publishes a token- or character-level confidence
for vision, or any OCR accuracy figure at all. In a model that never overwrites a value
(`design.py`, first value wins), a number entered with no confidence *is* the ADR-0019 failure mode.

#### 4.5 Licence and offline-on-Apple-Silicon

| Engine | Licence | Offline on macOS/Apple Silicon? |
|---|---|---|
| Tesseract | Apache-2.0 | ✅ |
| PaddleOCR | Apache-2.0 | ✅ |
| docTR | Apache-2.0 | ✅ |
| MinerU | custom *"MinerU Open Source License"* (was AGPLv3), commercial thresholds 100 M MAU / $20 M monthly revenue | ✅ macOS ≥ 14.0, MPS + mlx documented ([MinerU](https://github.com/opendatalab/MinerU)) |
| Surya | **code Apache-2.0, but weights are a modified AI-Pubs-Open-RAIL-M — free only under $5 M revenue** | ✅ (llama.cpp backend) — **licence flag** ([Surya](https://github.com/datalab-to/surya)) |
| olmOCR | Apache-2.0 code, 7 B VLM | ❌ requires *"a recent NVIDIA GPU… ≥12 GB VRAM"*, vLLM/CUDA 12.8 ([olmOCR](https://github.com/allenai/olmocr)) |
| Azure DI | proprietary | ❌ container is **x64-only** and must phone home for billing ([containers](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/containers/install-run)) |
| AWS Textract | proprietary | ❌ cloud only |
| Google Document AI | proprietary | ❌ GKE container marked *(Deprecated)* |

For a FreeCAD workbench that must run on a designer's Mac, that leaves **Tesseract, PaddleOCR, docTR,
MinerU and (with a licence caveat) Surya**. Everything else is a network dependency, and olmOCR is
out on hardware.

#### 4.6 There is no benchmark that measures this

**olmOCR-Bench and OmniDocBench are both first-party and both prose-only — neither has a drawing or
line-art category.** So there is **no first-party accuracy number for any engine on engineering
drawings**; Surya's 83.3 and MinerU's 95.39 are self-reported on prose documents. Any engine choice
here has to be validated on this repo's own sheets, because nobody else has measured it.

#### 4.7 The classical dimension-set literature (all private datasets)

The value→geometry linking problem was worked on long before deep learning, and the papers are worth
naming because the geometry rules they encode are still the rules:
Lai & Kasturi, ICDAR'93 ([10.1109/ICDAR.1993.395662](https://doi.org/10.1109/ICDAR.1993.395662));
Das & Langrana, ICDAR'95 and CVIU'97
([10.1006/cviu.1997.0537](https://doi.org/10.1006/cviu.1997.0537) — the one that actually links a
dimension to the geometry it measures); Gao, Tang & Liu, ICDAR'95; Dori & Velkovitch,
*Computers in Industry* 1999; Su, Song & Tai, CVPR'01; Xu, *CIRP JMST* 2024. **Every one uses a
private dataset**; the only public dataset in this space is in P&ID land
([Digitize-PID, arXiv:2109.03794](https://arxiv.org/abs/2109.03794)). That absence is itself a
finding: there is no public benchmark this repo could be scored against, so its own four sheets are
the benchmark.

### 5. What the benchmarks say a VLM is and is not good at here

- **AECV-Bench** (2026), 120 floor plans + 192 drawing-grounded QA pairs, unified protocol across
  frontier models: *"OCR and text-centric document QA are strongest (up to 0.95 accuracy), spatial
  reasoning is moderate, and symbol-centric drawing understanding — especially reliable counting of
  doors and windows — remains unsolved (often 0.40–0.55 accuracy)… current systems function well as
  document readers"* — and, by implication, badly as drawing readers.
  ([arXiv:2601.04819](https://arxiv.org/abs/2601.04819))
- **DesignQA** (2024), Formula SAE rules + CAD images + engineering drawings, evaluating GPT-4o,
  GPT-4, Claude Opus, Gemini 1.0, LLaVA-1.5: the models *"face challenges in recognizing technical
  components in CAD images, and encounter difficulty in analyzing engineering drawings."*
  ([arXiv:2404.07917](https://arxiv.org/abs/2404.07917))
- **Fine-tuned small beats zero-shot large, decisively.** Florence-2 at **0.23 B parameters**,
  fully fine-tuned on 400 expert-annotated drawings, versus zero-shot GPT-4o and Claude 3.5 Sonnet on
  GD&T extraction: **+29.95% precision, +37.75% recall, +52.40% F1, −43.15% hallucination**.
  ([arXiv:2411.03707](https://arxiv.org/abs/2411.03707))
- **The association problem is its own research task and is tractable.** ViRED frames
  image↔table correspondence in electrical drawings as *visual relation detection* with a vision
  encoder + object encoder + relation decoder, reporting **96% relation-prediction accuracy** — and
  notes that generic document understanding *"predominantly focus[es] on text as the main modality,
  which is not suitable for documents containing substantial image information."*
  ([arXiv:2409.00909](https://arxiv.org/abs/2409.00909))
- **Raster line art → vectors is a solved-enough research problem.** *Deep Vectorization of Technical
  Drawings* (ECCV 2020): cleaning stage, transformer estimating vector primitives, then an
  optimisation to fit final primitive configurations, on *"floor plans, architectural drawings, and
  2D CAD images."* ([arXiv:2003.05471](https://arxiv.org/abs/2003.05471))
  Classical baseline for comparison: Photo2CAD reconstructs 3D solids from 2D drawings with plain
  OpenCV, *"effective for simple shapes"* but *"does not address hidden lines"*
  ([arXiv:2101.04248](https://arxiv.org/abs/2101.04248)) — the hidden-line caveat is the same one
  OpenAI names about dashed lines, from the opposite direction.
- Adjacent, for the shape half: *Drawing2CAD* learns CAD command sequences from **vector** drawings
  ([arXiv:2508.18733](https://arxiv.org/abs/2508.18733)); *VectorGraphNET* segments technical drawings
  by treating the vector primitives as a graph ([arXiv:2410.01336](https://arxiv.org/abs/2410.01336));
  *Component Segmentation of Engineering Drawings Using Graph Convolutional Networks*
  ([arXiv:2212.00290](https://arxiv.org/abs/2212.00290)). All three take **vector input**, which this
  repo has for the dimension apparatus and lacks for the geometry.

### 6. Raster line art → vector geometry, offline on macOS

**Do not use potrace.** Two reasons, both first-party. Licence: **GPL v2-or-later**, and "Potrace" is
a registered trademark ([potrace](https://potrace.sourceforge.net/)). Function: it is an *outline*
tracer, and the author says so — *"Does Potrace provide centerline tracing? No, Potrace is not
designed to do centerline tracing, and for technical reasons, it is unlikely that this will change…
You could try Autotrace"* ([FAQ](https://potrace.sourceforge.net/faq.html)). On a 1–2 px engineering
stroke an outline tracer returns a closed loop hugging *both sides* of the line: a 100 mm edge comes
back as a ~200 mm-perimeter sliver, every T-junction becomes a topological blob, and you must
re-derive the centreline anyway. Inkscape's tracer is potrace, and Inkscape's own tutorial warns
*"the Tracer's purpose is not to reproduce an exact duplicate of the original image; nor is it
intended to produce a final product. **No autotracer can do that.**"*
([Inkscape tracing tutorial](https://inkscape.org/doc/tutorials/tracing/tutorial-tracing.html))

**Use skeletonisation, which is permissively licensed and arm64-clean.**

| Tool | Licence | API | Note |
|---|---|---|---|
| scikit-image `skeletonize` | BSD-3 | *"each connected component in the image is reduced to a single-pixel wide skeleton"*, `method` ∈ `zhang`/`lee` | [skimage.morphology](https://scikit-image.org/docs/stable/api/skimage.morphology.html) |
| scikit-image `medial_axis` | BSD-3 | ridges of the distance transform; `return_distance=True` gives **per-pixel stroke half-width** | same — lets you *classify* strokes (thin edge vs section line) |
| `cv2.ximgproc.thinning` | Apache-2.0 (OpenCV ≥ 4.5) | `THINNING_ZHANGSUEN` / `THINNING_GUOHALL` | needs `opencv-contrib-python`; arm64 wheels, no compile ([ximgproc](https://docs.opencv.org/4.x/df/d2d/group__ximgproc.html)) |
| autotrace `-centerline` | program GPL-2+, **library LGPL-2.1+**; `brew install autotrace` | curve fitter, not a topological skeleton — emits splines, no junction-connectivity guarantee | [autotrace](https://github.com/autotrace/autotrace) |

**OpenCV primitives, with the failure modes OpenCV itself documents:**

- `HoughCircles`: *"Usually the function detects the centers of circles well. **However, it may fail to
  find correct radii.**… you may set maxRadius to a negative number to return centers only… and find
  the correct radius using an additional procedure."*
  ([imgproc feature](https://docs.opencv.org/4.x/dd/d1a/group__imgproc__feature.html)) →
  **never read a hole diameter off `HoughCircles`**; use it to *find* the hole, then re-fit contour points.
- `HoughLinesP`: `maxLineGap` is *"maximum allowed gap between points on the same line to link them"*.
  A centreline or hidden line fragments unless the gap exceeds the dash period — and raising it that
  far starts bridging two genuinely separate collinear edges (two coaxial holes). **In this repo that
  whole failure class is avoidable**: the dashed centrelines are *vector*, so mask them out of the
  raster from the PDF before running Hough. `LineSegmentDetector` is the documented gradient/NFA
  alternative with sub-pixel endpoints and no accumulator quantisation.
- `approxPolyDP`: `epsilon` is *"the maximum distance between the original curve and its
  approximation"* ([imgproc shape](https://docs.opencv.org/4.x/d3/dc0/group__imgproc__shape.html)) —
  at 4.54 px/mm, `epsilon = 1 px` ⇒ ≤ 0.22 mm, `epsilon = 0.5 px` ⇒ ≤ 0.11 mm. **Set it from the
  tolerance, not by taste.** RDP cannot tell "curve" from "many corners", so fit arcs *before* it.
- `fitEllipse`: *"at least 5 points are required"*, and the docs note the conic basis *"includes lines,
  quadratics, parabolic and hyperbolic functions"*. A short fillet arc is numerically
  ill-conditioned — use a constrained circle fit (Taubin/Pratt) with tangency to the adjacent lines.

**Line/arc segmentation of a digitised polyline is a solved classical problem.** Rosin & West,
*Segmentation of edges into lines and arcs*, IVC 1989
([10.1016/0262-8856(89)90004-8](https://doi.org/10.1016/0262-8856(89)90004-8)); Chen, Ventura & Wu,
IVC 1996 ([10.1016/0262-8856(95)01042-4](https://doi.org/10.1016/0262-8856(95)01042-4)); and, for a
globally optimal rather than heuristic result, **Horng & Li, *A dynamic programming approach for
fitting digital planar curves with line segments and circular arcs*, PRL 2001**
([10.1016/S0167-8655(00)00104-5](https://doi.org/10.1016/s0167-8655(00)00104-5)). The domain-specific
baseline is Dori & Liu's sparse-pixel vectorisation, TPAMI 1999
([10.1109/34.754586](https://doi.org/10.1109/34.754586)), evaluated with the Vector Recovery Index
([10.1007/s001380050045](https://doi.org/10.1007/s001380050045)). The **GREC Arc Segmentation
Contests** are the closest published baseline for "find arcs in scanned line drawings", and note that
GREC'11 ran specifically on *multi-resolution* scans
([10.1007/978-3-642-36824-0_18](https://doi.org/10.1007/978-3-642-36824-0_18)) — because arc recovery
degrades with DPI, which is the regime this repo is stuck in.

**Achievable accuracy at 4.54 px/mm — and the trap.** Precision is not the problem: a thresholded
stroke centre is known to ≈ ±0.5 px = ±0.11 mm, and a line fitted through 454 centreline pixels
averages that far down. **Bias is the problem**: anti-aliasing asymmetry, corner rounding, and
skeleton retraction at endpoints of roughly half a stroke width — ≈ **0.22 mm**, comparable to the
entire quantisation budget. Realistic expectation is **corner/endpoint coordinates good to
~0.2–0.5 mm**, and short-fillet radii considerably worse. The rule that follows is the important
one: **never emit traced geometry as the authoritative number.** Use the trace for *topology and
association* only, take every *value* from the vector text, and snap the traced geometry onto those
exact values — which is what a Sketcher-based FreeCAD workflow wants anyway.

**Deep learning option, and why it is not the first move.** *Deep Vectorization of Technical
Drawings* (ECCV 2020, [arXiv:2003.05471](https://arxiv.org/abs/2003.05471),
[code MPL-2.0](https://github.com/Vahe1994/Deep-Vectorization-of-Technical-Drawings)) is the
state-of-the-art pipeline: deep cleaning → transformer estimating vector primitives per patch →
optimisation of primitive configurations, trained on ABC and Precision Floorplan. But the README
states *"Requirements: Linux system"*, pins `torch==1.7.1`, and depends on `chamferdist` (a CUDA
extension), with a Dockerfile wanting `--shm-size 128G`. **Not an offline-Apple-Silicon option
today** — a reference to port from, not a dependency to add.

### 6b. The lossless alternative: ask for DXF

This is the strongest single finding in the whole report, and it makes most of section 6 optional.

**PDF has no dimension semantics, by specification.** ISO 32000-1 §14.8.4 Standard Structure Types
defines `Document`, `Part`, `Sect`, `P`, `H`, `Figure`, `Table`… and notes *"most of the standard
element types are designed primarily for laying out text"*. There is **no** `Dimension`, no `Leader`,
no association construct. The only CAD-aware construct in the entire spec is §12.9 *Measurement
Properties* — a viewport `/VP` array with an optional `Measure` dictionary encoding **scale and
units**, not entities. ([ISO 32000-1](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf))
Worth a cheap check: the page's `/VP` may hand over the 1.7573 pt/mm this repo measured by hand.

**DXF, by contrast, hands over the association.** From the Autodesk DXF Reference, ENTITIES →
DIMENSION ([DXF reference](https://images.autodesk.com/adsk/files/autocad_2012_pdf_dxf-reference_enu.pdf)):

> *"The point **(13,23,33)** specifies the **start point of the first extension line** and the point
> **(14,24,34)** specifies the **start point of the second extension line**. Point (10,20,30)
> specifies the dimension line location. The point (11,21,31) specifies the midpoint of the dimension
> text."*

plus group code **`42` = "Actual measurement (optional; read-only value)"**, **`1` = the text override
("if null or `<>`, the dimension measurement is drawn as the text")**, `70` = dimension type
(0 rotated/horizontal/vertical, 1 aligned, 2 angular, 3 **diameter**, 4 **radius**, 5 angular-3-point,
6 ordinate), and `15,25,35` = the definition point on the circle for radial/diameter dimensions.

So a DXF gives, per dimension: **the value, its type (including "this is a diameter"), and the two
points it was measured between — in the same coordinate system as the `LINE`/`ARC`/`CIRCLE`/
`LWPOLYLINE` entities of the view.** Association degenerates to a nearest-entity lookup with
millimetre-exact anchors. There is no tracing step, no 186 DPI ceiling, no `Ø`-is-geometry problem
(dimtype 3 *says* diameter), and no OCR anywhere.

**`ezdxf` is the reader, and its licensing is clean**: *"MIT-License"*, *"OS independent"*, R12
through R2018, with binary wheels on PyPI for macOS
([ezdxf](https://ezdxf.readthedocs.io/en/stable/)). The `Dimension` entity exposes `dxf.dimtype`,
`dxf.defpoint`, `dxf.defpoint2` (*"the start point of the first extension line"*), `dxf.defpoint3`
(*"the start point of the second extension line"*), `dxf.text_midpoint`, `dxf.actual_measurement`,
`dxf.text`, `dxf.oblique_angle`
([Dimension API](https://ezdxf.readthedocs.io/en/stable/dxfentities/dimension.html)).
Two documented gotchas to plan for: `actual_measurement` (42) is *"an optional attribute and often
not present"* → recompute from `defpoint2`/`defpoint3`; and the drawn dimension picture lives in an
anonymous block referenced by group code `2`, so a naive entity walk double-counts arrowheads. Also
check `Dimension.is_dimensional_constraint` and skip those.

**STEP AP242** (ISO 10303-242) is the equivalent ask if the user can export 3D with PMI — it carries
semantic dimensions and tolerances as *data* rather than as drawn lines, which is the right substrate
when the goal is a solid model rather than a 2D re-draw.

### 6c. SVG-as-text for LLMs: the measurements say don't

The question "should we send the drawing as SVG instead of a picture" has been benchmarked, and the
answer is a clear no *for raw SVG*, and a clear yes *for derived primitives*.

- **VGBench** ([arXiv:2407.10972](https://arxiv.org/abs/2407.10972)), 4 279 understanding QAs across
  SVG / TikZ / Graphviz. GPT-4 zero-shot: **SVG 54.9% vs TikZ 81.0% vs Graphviz 84.5%** — the same
  pictures, different notations. Their conclusion: *"TikZ and Graphviz include more high-level
  semantics compared to SVG, which is composed of low-level geometry."*
  Crucially for sizing, GPT-4's SVG accuracy **decays monotonically with input length**:
  1–1000 chars **65.7** · 1000–2000 **61.0** · 2000–3000 **57.9** · 3000–4000 **56.6** · >4000 **57.2**,
  and the paper notes SVG is *"most sensitive to the length"* while the higher-level formats stay flat.
- **SGP-Bench** ([arXiv:2408.08313](https://arxiv.org/abs/2408.08313)): average accuracy across all
  models is *"below 70% (ranging from 30% to 67%) on SVG"*. The number to remember is **SGP-MNIST** —
  1000 SVG programs rendering MNIST-like digits, chance 10%: GPT-4o **13.0**, GPT-4t 10.6,
  Llama3-70B 10.0. The authors attribute the collapse to *"convoluted, irregular path trajectories"*
  with *"enclosed loops to represent the thickness"*. **An outline trace of a part profile is
  structurally the same object as those MNIST programs** — this is the single most damning datapoint
  against "trace the view and send the SVG".
- **VDLM** ([arXiv:2404.06479](https://arxiv.org/abs/2404.06479)) states it plainly: *"existing large
  language models are unable to understand raw SVGs in zero-shot settings"*, with GPT-4 on raw SVG
  scoring 0.45/0.47 on binary line-or-angle tasks — near chance. Their second finding is the
  actionable one: *"the SVG code becomes excessively verbose… learning a model to directly comprehend
  the raw SVG code of an entire image poses significant challenges."* Their fix, **Primal Visual
  Description**, is a JSON list of typed primitives with attributes (shape, position, measurement),
  and it lifts task accuracy to 0.66–0.67 against GPT-4V's 0.53.

**Token arithmetic, which settles it for this repo.** A path segment `M 123.45 678.90 L 234.56 789.01`
is ~15–20 BPE tokens; a traced profile of 500–2000 segments is **10–40 k tokens**, doubled again if
the trace is an *outline* rather than a centreline — i.e. squarely in the >4000-char bucket where
VGBench measures degradation. Against that, a **1000×1000 px crop costs 1296 visual tokens** on Claude
([vision](https://platform.claude.com/docs/en/build-with-claude/vision)). **A raster crop of a view is
cheaper than its own SVG, and much cheaper than its outline trace.** The right answer is crop *plus*
structured text, never SVG instead of the picture.

**What to send instead**, best first: (1) a **JSON list of typed primitives in millimetres** —
VDLM's PVD, generalised to this domain: `edge_7: LINE (12.00,0.00)→(12.00,45.00), len 45.00`;
`hole_2: CIRCLE c=(30.00,22.50) r=4.00`; `dim_3: 45.00 measures edge_7, extension lines at
(12.00,0.00) and (12.00,45.00)` — roughly 20 tokens per entity, already in the units the answer is
wanted in. (2) Simplified path lists only as a fallback, after removing outline duplication.
(3) Keep the raster crop alongside.

### 7. Cross-checking patterns that are actually documented

- **Self-consistency.** Sample the reading *k* times at non-zero temperature and take the majority
  answer per field; introduced as self-consistency decoding, [arXiv:2203.11171](https://arxiv.org/abs/2203.11171).
  For a numeric field this is cheap and directly attacks "108 read as 100": a digit misread is
  usually not stable across samples, whereas a correct read is. Note Google's warning that on
  Gemini 3 you should **not** force low temperature — *"we recommend removing this parameter and
  using the Gemini 3 default of 1.0 to avoid potential looping issues"*
  ([Gemini 3](https://ai.google.dev/gemini-api/docs/gemini-3)), which happens to make sampling-based
  consensus the natural pattern rather than a hack.
- **Give the model the extracted text alongside the crop.** This is what Gemini 3 now does natively
  and for free on PDFs (*"text natively embedded in the PDF is extracted and provided to the model"*,
  and *"you are not charged for tokens originating from the extracted native text"*,
  [document processing](https://ai.google.dev/gemini-api/docs/document-processing)), and what
  `printed_on_the_sheet` already builds for Claude. The model's job becomes *choose from this list*,
  not *read this pixel*.
- **Make it point at what it used.** Anthropic Citations with custom content chunks turns "which
  callout did you use" into an API-level guarantee rather than a prompt request
  ([citations](https://platform.claude.com/docs/en/build-with-claude/citations)). Where the pointer
  must be spatial rather than textual, ask for **absolute pixel** boxes on Claude
  ([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)) or
  `[0, 1000]`-normalised `box_2d` on Gemini
  ([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding)), then verify the
  box actually contains the text-layer word it claims — a check this repo can do exactly, because it
  has both boxes.
- **Constrain the shape of the answer.** Constrained decoding on both Anthropic
  ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs))
  and Gemini ([image understanding](https://ai.google.dev/gemini-api/docs/image-understanding))
  removes malformed-JSON retries entirely; it does not improve the reading, and should not be
  mistaken for accuracy.
- **Crop, don't zoom the sheet.** Anthropic's own remedy for fine targets is *"crop the region of
  interest and send the crop (offset returned coordinates by the crop origin)"*
  ([coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)).
  `sheet_reader.py`'s central design decision is vendor-endorsed.

## What this would mean for FreeCadCopilot

### The one-line version

Stop trying to make the model *read* the sheet better. The sheet already told us the numbers exactly.
Make the model *associate* — and give it geometry, in millimetres, instead of a picture.

### Immediate, cheap, and independently checkable

**A. Fix `MAX_EDGE`.** `core/sheet_reader.py:80` sets `MAX_EDGE = 1400` with the comment *"Providers
downscale above their own cap — 1568 px for Anthropic"*. That reads the wrong limit. The binding
constraint is the visual-token budget: a 1400×1400 crop costs 2500 tokens against a 1568-token
standard-tier budget and is downscaled to **1092×1092**. Two correct fixes, both small:

- Replace the single `MAX_EDGE` with Anthropic's published `resized_size(w, h, max_edge, max_tokens)`
  and render each crop to the size the model will actually see. Then the render *is* the model's
  view, and any returned pixel coordinate maps 1:1 onto it with no rescaling
  ([reference implementation](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)).
- Or, if a single constant is wanted, make it tier-aware: **1092** for standard, **1932** for
  high-resolution (Claude 4.7+). Cost: one function, no behaviour change elsewhere. Verify by
  asserting `resized_size(*crop.size) == crop.size` for every crop the module emits.

**B. Cap the render at the source raster's DPI.** Since every view is an embedded PNG at 186 DPI,
`_fits()` choosing a DPI to hit `max_edge` can *upsample* — spending tokens and latency on
interpolated pixels that contain no information. Add a ceiling at the maximum DPI of the images
placed inside the region (`page.get_images` / `get_image_info` gives placement rect and pixel size).
Verify: no crop of a view exceeds 186 DPI relative to its region rect. Note that on both Claude tiers
a real 712×585 view crop is never resized (546 tokens), so this costs nothing in fidelity.

**C. Send crops as PNG, and keep the request under 20 image blocks.** Anthropic documents that heavy
JPEG compression damages text legibility, and that >20 image/document blocks in one request triggers
a stricter ~2000 px per-image dimension limit
([vision](https://platform.claude.com/docs/en/build-with-claude/vision)). A five-region, two-page
sheet plus overviews crosses 20.

### The real change: the reading loop becomes a matching loop

The evidence — measured on this repo's sheets, and independently in the literature
([arXiv:2506.17374](https://arxiv.org/abs/2506.17374), [arXiv:2510.21862](https://arxiv.org/abs/2510.21862),
[arXiv:2409.00909](https://arxiv.org/abs/2409.00909), [arXiv:2601.04819](https://arxiv.org/abs/2601.04819))
— points at one pipeline:

1. **Extract the dimension apparatus as geometry, not pixels.** The green stroked paths *are* the
   dimension and extension lines; the blue paths are leaders and section arrows. For each candidate
   dimension: the dimension line's two endpoints, the two extension lines it terminates on, and the
   text-layer word whose bbox sits on or beside it. `sheet_reader.py` already has `_leaders`,
   `_segments`, `_chained`, `_fitted` and `_millimetres_per_point` — this is assembly, not new
   machinery. **This is the free equivalent of the YOLOv11-obb stage** the literature spends a
   1367-drawing annotated dataset to train; the oriented boxes are already in the file.
2. **Emit a `Dimension` record with a *measured span*, in millimetres.** `value` from the text layer
   (never read), `span_mm` = |p2 − p1| × 0.569 mm/pt from the vector dimension line, `p1`/`p2` in
   sheet points, and the view it lies in. Now every dimension carries **its own check**: a `40`
   whose dimension line spans 40.0 mm at the sheet's scale is self-consistent; a `108` read as `100`
   would have to survive a span that says 108. **This alone kills ADR-0019's failure**, because the
   failing quantity is now cross-checked by an independent measurement of the same thing. (The
   drawing may be dimensioned not-to-scale, so this must be a *flag*, not an override — which fits
   `design.py`'s existing "conflicts recorded, not resolved" contract exactly.)
3. **Trace the 186 DPI view raster into a primitive list, in millimetres.** At 4.54 px/mm, a traced
   contour is good to ≈ ±0.25 mm. Emit lines, arcs and circles, not pixels. Even a coarse trace is
   30× more accurate than the 8 mm error being chased, and it converts the shape question from
   perception into geometry.
4. **Ask the model the association question only.** Give it: the primitive list (mm), the dimension
   records (value + measured span + endpoints), the crop as a picture for context, and a schema.
   Ask *"which traced edge does dimension D-7 measure, and what feature does that make it"* — never
   *"what does this say"*. Constrain the answer with structured outputs
   ([Anthropic](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
   [Gemini](https://ai.google.dev/gemini-api/docs/image-understanding)).
5. **Make every claim cite a dimension id.** Feed the dimension records as Anthropic *custom content*
   chunks with citations enabled; a `design.py` field whose supporting chunk is missing or whose
   cited value differs from the record is rejected before it can win the "first value wins" race
   ([citations](https://platform.claude.com/docs/en/build-with-claude/citations)). This generalises
   `uncited()` from a post-hoc audit into a gate.
6. **Consensus only where a judgement remains.** Self-consistency over *k* samples on the association
   and the feature type ([arXiv:2203.11171](https://arxiv.org/abs/2203.11171)) — not on the numbers,
   which are no longer being guessed at.
7. **Symbols stay a geometry question.** `Ø` is a circle-and-slash path adjacent to a numeral's bbox;
   detect it as a glyph-shaped path near a number rather than hoping OCR emits U+00D8. Same for `±`,
   `▽`, and GD&T frames (a rectangle subdivided by vertical rules, containing a datum letter). This
   is the object-detection framing the literature uses
   ([arXiv:2204.13277](https://arxiv.org/abs/2204.13277)).

### Raster attachments (the worst case) degrade honestly

With no text layer, none of the above is available and the pipeline is genuinely in
VLM-plus-OCR territory. The right posture is not to pretend otherwise: run detection first (rotation
aware), then read patches, then attach a **confidence** — which is available from a dedicated OCR
engine per word ([Azure DI](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout))
and is available from *no* VLM provider. A number without a confidence, in a model that never
overwrites, is the ADR-0019 failure by construction. On raster input the reading should be entered as
low-confidence and consensus-sampled, or entered into `not_defined` rather than `read`.

### Rejected options, and why

- **Send the PDF as a `document` block and let the provider handle it.** Rejected. Anthropic
  rasterises server-side *"at dimensions you don't control"* and says coordinates *"can't be reliably
  mapped back onto the page"*; Gemini caps pages at 3072×3072 and states there is *"no… performance
  improvement for pages at higher resolution"*. The repo already rejected this; the first-party docs
  confirm it was right. ([Anthropic coordinates](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates),
  [Gemini document processing](https://ai.google.dev/gemini-api/docs/document-processing))
- **Render the PDF at a higher DPI to make the geometry legible.** Rejected on measurement: the views
  are embedded rasters at 186 DPI. Rendering the page at 600 DPI interpolates. 186 DPI is the
  ceiling and no provider setting changes it.
- **Bolt an OCR engine onto the vector PDF for the numbers.** Rejected. The text layer is exact and
  carries bboxes; OCR can only introduce error where there was none. OCR earns its place only on
  raster attachments and, arguably, never for symbols — which are paths, not characters.
- **Push the whole sheet at `high` / `ultra_high` resolution and prompt harder.** Rejected. AECV-Bench
  puts symbol-centric drawing understanding at 0.40–0.55 with frontier models under a unified
  protocol ([arXiv:2601.04819](https://arxiv.org/abs/2601.04819)); DesignQA reaches the same
  conclusion ([arXiv:2404.07917](https://arxiv.org/abs/2404.07917)). More pixels does not move a
  capability that is missing.
- **Fine-tune a small VLM (Florence-2 / Donut) on drawings.** Not rejected on merit — it is the best
  published result for this exact task, +52% F1 over zero-shot GPT-4o
  ([arXiv:2411.03707](https://arxiv.org/abs/2411.03707)) — but rejected *for now* on cost: it needs
  400–1400 expert-annotated drawings and a training loop, and this repo can get most of the same
  benefit for free because its detector stage is already in the PDF vector layer. Revisit if raster
  attachments become the main input.
- **Serialise the drawing as raw SVG and send it as text.** Rejected in favour of a derived primitive
  list. Path `d` data is mostly dashes and hatching, carries no semantics the model can use, and pays
  full text-token price with no tiling discount. The papers that succeed on vector input
  ([arXiv:2410.01336](https://arxiv.org/abs/2410.01336),
  [arXiv:2508.18733](https://arxiv.org/abs/2508.18733)) consume *primitives*, not markup.
- **Ask the model for normalised `0–1000` boxes to stay provider-neutral.** Rejected for Claude,
  which documents the opposite: *"Claude does not work well when you ask for normalized
  coordinates."* Coordinate convention must be per-provider (Claude: absolute pixel `[x1,y1,x2,y2]`;
  Gemini: `[0,1000]` `[ymin,xmin,ymax,xmax]`).
  ([Anthropic](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates),
  [Gemini](https://ai.google.dev/gemini-api/docs/image-understanding))
- **Trust a VLM's stated confidence.** Rejected: no provider publishes a token- or character-level
  confidence for vision, and none publishes an OCR accuracy figure at all. Confidence has to come
  from an OCR engine's per-word score or from sampling consensus.

## Open questions

1. **Is the 186 DPI raster-view structure a SolidWorks *export-setting* artefact?** If the same part
   can be exported with vector geometry, the whole shape problem disappears and the right fix is a
   documentation line, not a tracing stage. Worth one experiment before building anything.
2. **Would asking the user for DXF/STEP instead of PDF make this trivial?** A DXF retains `DIMENSION`
   entities with their measured value and definition points — the association is *in the file*. The
   cost/benefit of "please export DXF" versus a tracing pipeline is unmeasured here.
3. **How reliable is the green/blue stroke-colour convention?** The dimension-apparatus extraction
   above leans on it. If it is a SolidWorks default rather than a guarantee, the extraction needs a
   colour-independent fallback (e.g. paths terminating in arrowhead-shaped filled triangles).
4. **Does the measured-span check ever fire falsely?** Detail views, broken views and
   "NOT TO SCALE" dimensions all legitimately break span↔value agreement. Needs measuring across all
   four test sheets before it is allowed to gate anything.
5. **Which Claude tier is the code actually hitting?** The 1092-vs-1932 fix depends on it, and the
   repo's model selection was not inspected for this report.
6. **What is the achievable accuracy of a contour trace at 4.54 px/mm on these specific views**, once
   hidden lines, centrelines and hatching are present? Both OpenAI's limitations list and Photo2CAD
   name hidden/dashed lines as the failure point.
7. **Gemini's `media_resolution` guidance says PDF quality "saturates at medium"** — measured on
   standard documents. Untested on line art, and cheap to test.
8. **Is per-word OCR confidence actually calibrated** on drawing-like imagery, or only on prose? The
   vendor docs report the field; none of them claim calibration.
