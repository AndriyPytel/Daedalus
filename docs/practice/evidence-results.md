# Evidence: Measured Results for the Practice Report

Every number below traces to a test, a script, a log file, or a git commit in this
repo. Where a number is a *claim* (README prose, pricing research) rather than a
direct measurement, it is marked so and traced to its actual source.

## 1. Table of measured numbers

| # | Metric | Value | Produced by | Date |
|---|---|---|---|---|
| 1 | Embedded raster DPI in all four Model Mania sheets | 186 DPI (all four sheets) | `docs/research/00-measured-sheet-anatomy.md` §1, measured with pymupdf on `tests/modelmania/*.pdf` | doc undated, sheets in repo since Aug 2026 |
| 2 | Vector path count per sheet | 25 / 23 / 32 / 33 (mm25-p1-1 / 2024 / 2023 / mm25-p2) | same, `page.get_drawings()` count | same |
| 3 | Provider image cap reached at | 1568 px on an 11×8.5in sheet = 143 DPI | README.md L80-84, measured on `mm25-p1-1.pdf` against the provider's own resize | README (undated prose) |
| 4 | Ink height of a dimension digit at that cap | 14 px | README.md L82 | same |
| 5 | Per-sheet crop DPI, set by callout text size | 279 DPI (12.9 pt dimension text) vs 492 DPI if driven by the 4.1 pt title-block paragraph | README.md L108-112 | same |
| 6 | Title-block text share of sheet's 89 words | 28 words (31%) | README.md L110 | same |
| 7 | pt/mm scale calibration, corrected | 212.60 pt / 150 mm = 1.41733 pt/mm (≈ printed 1:2 scale); raster calibration gave 1.418 independently | `docs/research/00-measured-sheet-anatomy.md` §4 | same |
| 8 | Rejected first estimate (bug, corrected in same doc) | 1.7573 pt/mm — was measured off the full dimension line, ~24% too high | same | same |
| 9 | Ink-bbox size error, view calibration | true 150×50 mm, measured 150×50.45 mm → 0.9% error | same | same |
| 10 | Two bore diameters, measured off raster | 39.27 × 39.00 mm (nominal Ø40, print reads `40`) → ~2% error | same | same |
| 11 | Estimated raster resolution | 1 px ≈ 0.18–0.22 mm | same | same |
| 12 | Line-work placement error found on the 2023 pipe clamp | arm built 17.5 mm out (should sit 10.0 mm below ring axis; run assumed centred) | README.md L118-124, `tests/recorded/reading_2023_clamp.json` referenced by `modelmania_harness.the_2023_clamp_as_read()` | README (undated) |
| 13 | Recorded descriptions vs their own sheet: invented numbers | 4–8 per description, 32 descriptions | `tests/test_modelmania_evidence.py::test_recorded_descriptions_keep_their_own_sheet_and_fail_on_the_others` (passes) | test in repo, run 2026-08-22 |
| 14 | Same descriptions vs the *other three* sheets: invented numbers | 150–180 | same test | same |
| 15 | Outline count across all four Model Mania sheets, all build+solve | 142 total (25 + 21 + 52 + 44 per sheet) | `tests/test_modelmania_evidence.py::test_the_four_sheets_keep_their_measured_reading_verdicts` (`@pytest.mark.freecad`, passes) | same |
| 16 | Face-to-face distances that reach outlines | 4 + 2 + 3 + 2 = 11 (10 sheets have ≥1) | same test | same |
| 17 | Sketch-solver movement (recorded vs re-solved), median | 0.15–0.30 mm | same test | same |
| 18 | Movement, 90th percentile | 0.50–0.75 mm | same test | same |
| 19 | Movement, worst case | 1.0–1.5 mm | same test | same |
| 20 | 2023 clamp sheet: strings printed vs inside the title block | 230 printed strings, 121 (53%) inside the title block | `tests/test_modelmania_evidence.py::test_the_document_carries_nothing_off_the_title_block_and_no_dead_feature` (passes) | same |
| 21 | Callouts/contours reaching Design vs cut out | 115 dimension callouts, 961 traced contours kept; 1,289 unprovable entities cut | `tests/test_modelmania_evidence.py::test_nothing_the_document_needs_is_cut_out_of_what_design_is_shown` (passes) | same |
| 22 | 2023 clamp: distilled features / Acceptance entries | 11 features, 46 Acceptance entries | `tests/test_modelmania_evidence.py::test_the_recorded_run_ends_in_the_document_it_ended_in_before_the_cut` (passes) | same |
| 23 | Total tests collected (repo as of this run) | 1,432 test cases (953 distinct, non-parametrized functions among the 1,040 non-FreeCAD ones) | `pytest --collect-only`, run 2026-08-22 | 2026-08-22 |
| 24 | Non-FreeCAD ("Level 1"-style) subset | 1,040 collected cases (392 deselected as `freecad`) | `pytest -m "not freecad" --collect-only`, run 2026-08-22 | 2026-08-22 |
| 25 | Git commit count | 286 commits | `git rev-list --count HEAD` | HEAD as of 2026-08-22 |
| 26 | Commit date span | 2025-10-26 → 2026-08-21 | `git log --format=%ad` | 2026-08-22 |
| 27 | Distinct FCC ticket ids referenced in commit subjects | 123 | `git log --oneline \| grep -oE 'FCC-[0-9]+' \| sort -u \| wc -l` | 2026-08-22 |
| 28 | Recorded run sessions in `logs/sessions/` | 436 `.jsonl` files, 2026-08-05 → 2026-08-22 | `ls logs/sessions`, file mtimes | 2026-08-22 |
| 29 | Sessions carrying at least one `cost`/other tracked event | 31 of 436 | parsed from `logs/sessions/*.jsonl` | 2026-08-22 |
| 30 | Total metered LLM spend across all logged sessions | $81.70 | sum of `cost.usd` fields, `logs/sessions/*.jsonl` (203 cost events) | 2026-08-22 |
| 31 | Spend by model | opus $74.58, sonnet $7.12, `openai/qwen3.8-max` $0.00, `openai/kimi-k3` $0.00 | same | same |
| 32 | Spend by phase | design $29.56, plan $10.88, execute $35.90, assertion $4.50, review $0.86 | same | same |
| 33 | Review verdicts recorded | 13 pass / 7 fail (20 total) | `kind: review_verdict` events, `logs/sessions/*.jsonl` | 2026-08-22 |
| 34 | Assertion outcomes recorded | 7 held (`ok: true`) / 13 failed (`ok: false`) / 1 unlabeled, of 21 total | `kind: assertion` events, `logs/sessions/*.jsonl` | 2026-08-22 |
| 35 | `blocked` events (run halted needing user input) | 46 | `kind: blocked`, `logs/sessions/*.jsonl` | 2026-08-22 |
| 36 | `part` events (a part reached Apply) | 23 | `kind: part`, `logs/sessions/*.jsonl` | 2026-08-22 |
| 37 | `render` events (a viewport/rasteriser capture) | 66 | `kind: render`, `logs/sessions/*.jsonl` | 2026-08-22 |
| 38 | `error` events logged | 323 | `kind: error`, `logs/sessions/*.jsonl` | 2026-08-22 |
| 39 | `preflight_failed` events | 142 | `kind: preflight_failed`, `logs/sessions/*.jsonl` | 2026-08-22 |
| 40 | `logs/parts/*.yaml` evidence/description snapshots | 66 files (recorded design-doc snapshots per sheet/run) | `ls logs/parts` | 2026-08-22 |

Items 23-40 are this run's own measurements (produced while writing this report),
not pre-existing project claims; items 1-22 restate numbers already asserted by the
project's own docs/tests, with the test file that currently enforces each one.

**Not a measurement — pricing research.** `docs/research/vlm-cost-quality-analysis.md`
lists per-model $/1M-token prices (e.g. Claude Sonnet 4 API $3/$15, GLM-4.6V-FlashX
$0.04/$0.40) copied from OpenRouter/Z.ai listings, not from a run of this project. It
is market research for a model-choice decision, not evidence of what FreeCAD Copilot
itself costs to run — item 30-32 above (measured, from real sessions) is the honest
number for that.

## 2. Test suite state

Command: `.venv/bin/python -m pytest -q`, full output in `/tmp/fcc-pytest.txt`.

- **Collection succeeds cleanly**: 1,432 test cases (`pytest --collect-only`).
- **The full run does not complete in this environment.** Three separate full-suite
  attempts (this run and two earlier ones outside this session, per the parent's
  process list) all stall at the same point: **62%**, inside `tests/test_panel_level2.py`.
  A `-v` run isolates the exact stall point: it hangs on
  `tests/test_panel_level2.py::test_a_wrapped_message_gets_the_height_its_text_needs`
  and never returns — an offscreen-Qt layout test that appears to deadlock this
  machine's Qt/FreeCAD combination rather than fail. Everything printed up to that
  point is `.` (pass) with 4 `s` (skip), zero `F` (fail), zero `E` (error).
- **Non-FreeCAD subset** (`pytest -q -m "not freecad"`, 1,040 collected cases): ran to
  100% progress with all dots and no failures visible in the output, but the process
  itself was killed (`returncode -9`, SIGKILL) before printing the final summary line
  — consistent with either this machine's memory pressure or an external `pkill`
  issued during triage of the stalled full run (both happened during this session).
  **No pass/fail count for the non-FreeCAD subset can be certified**, only that no
  failure (`F`/`E`) character appeared before the kill.
- **README's stated counts** ("Level 1: 436 tests, 8 seconds"; "Level 2: 209 tests,
  2.5 minutes") do not match this run: collection now finds 1,040 non-`freecad` cases
  and 392 `freecad`-marked ones. The suite has grown since the README paragraph was
  written; treat the README figures as stale, not current.

## 3. Run statistics from `logs/`

From `logs/sessions/*.jsonl` (436 session files, timestamps 2026-08-05 to 2026-08-22),
parsed as newline-delimited `{"at", "kind", "data"}` events:

| Event kind | Count | What it records |
|---|---|---|
| `thought` | 147,249 | LLM reasoning tokens streamed to the log |
| `tool_call` / `tool_result` | 916 / 915 | Tool invocations against the FreeCAD document |
| `step_started` | 412 | Plan steps begun |
| `applied` | 358 | Steps that reached Apply |
| `error` | 323 | Errors surfaced during a run |
| `attached` | 303 | Attachments (drawings/images) added to a turn |
| `cost` | 203 | Metered LLM calls with a `usd` figure |
| `preflight_failed` | 142 | Preflight rejections before Apply |
| `intent` | 117 | Distilled user intents |
| `gate` | 113 | Plan-gate decisions |
| `question` / `answer` | 93 / 96 | Clarifying Q&A turns |
| `plan_revised` | 71 | Plan revisions |
| `render` | 66 | Viewport/rasteriser captures |
| `call_started` | 65 | LLM calls begun |
| `plan` | 64 | Plans produced |
| `design` | 59 | Design-phase turns |
| `blocked` | 46 | Runs halted needing user input |
| `part` | 23 | Parts reaching Apply |
| `assertion` | 21 | Assertion-phase verdicts |
| `review_verdict` | 20 | Review-phase verdicts |
| `reading` | 3 | Drawing readings |
| `outstanding` | 2 | Open questions left at end of run |

**Cost.** 203 metered calls total **$81.70** logged spend, only in the 31 sessions
(of 436) that carry `cost` events — most session files are logic/UI test runs with no
LLM calls, not user sessions.

- By model: opus $74.58, sonnet $7.12, `openai/qwen3.8-max` $0.00, `openai/kimi-k3`
  $0.00 (the last two are free-tier/self-hosted routes with no metered cost).
- By phase: execute $35.90, design $29.56, plan $10.88, assertion $4.50, review $0.86.

**Success rate.** Of the sessions that reached a verdict: **review** 13 pass / 7 fail
(65% pass); **assertion** 7 held / 13 failed / 1 unlabeled (of 21, i.e. ~33% clean
pass — most of these are FCC-142/144-era "measured the snap band" runs deliberately
producing both outcomes, not a representative production pass rate).

**Wall time.** No duration field exists in any event; only `at` (a timestamp) is
recorded per event, with no run-start/run-end pairing that a script currently
computes. A wall-clock number could be derived by diffing first/last `at` per session
file, but no test or doc in the repo already does this, so no wall-time figure is
claimed here.

`logs/parts/*.yaml`: 66 files, mostly paired `<sheet>.yaml` (a description) /
`<sheet>-evidence.yaml` (what backs each field) snapshots recorded per Model Mania
sheet per run — these are the raw material the `test_modelmania_evidence.py` numbers
above are computed from, not additional independent measurements.

## 4. What CANNOT be claimed

- **No certified full-suite pass count.** The suite has never been observed to finish
  end-to-end on this machine; `tests/test_panel_level2.py::test_a_wrapped_message_gets_the_height_its_text_needs`
  hangs indefinitely (offscreen Qt/FreeCAD interaction), and the non-FreeCAD subset
  was killed before its summary line printed. "1,040 tests, all passing" is not a
  claim this run can make — only "no failure character was seen before the kill."
- **No wall-clock timing for a run, a phase, or the test suite** exists in any log or
  script output. README's "8 seconds" / "2.5 minutes" for Level 1/2 cannot be
  reproduced or verified from anything currently in the repo.
- **No accuracy/quality benchmark comparing VLM providers.** `vlm-cost-quality-analysis.md`
  has empty `?` cells for "AI idx" and "3D Elo" for every Z.ai model, and states no
  model was actually run against `tests/modelmania/*.pdf` — it recommends testing,
  it does not report a test.
- **No aggregate success rate for "a run building the right part."** The 13/7 review
  split and 7/13/1 assertion split come from a small, non-random set of sessions
  (mostly deliberate FCC-142/144-era measurement runs), not a tracked production
  metric; no script in the repo computes an overall build-success rate.
- **No cost-per-drawing or cost-per-successful-part figure exists.** $81.70 total
  logged spend cannot be safely divided by "23 parts reached Apply" into a
  meaningful unit cost — `cost` events are calls, not full runs, and many of the 436
  session files carry no cost data at all (i.e. this $81.70 undercounts anything run
  through an unmetered/subscription route, and the FreeCAD Copilot README explicitly
  says the subscription route reported $0 until a recent commit, `35bf757`/`01af507`,
  added keyed-call cost estimation).
- **The 186 DPI / calibration numbers in `00-measured-sheet-anatomy.md` are scoped to
  four specific PDFs** (`tests/modelmania/*.pdf`) and are not claimed, in that
  document or here, to generalize to drawings from other sources or CAD packages.
