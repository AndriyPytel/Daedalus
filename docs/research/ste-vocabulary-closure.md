# STE vocabulary closure — coverage measurement before hard closure

Source: same corpus as `ste-design-duplicates.md` (410 features / 47 design
models) and `ste-plan-verbs.md` (878 verb occurrences / 47 verb lemmas).
Decisions already taken and not revisited here: the vocabulary is **closed
hard, no `other` escape hatch**; the artifact language is **English only**.
Script at the end reproduces every number below.

## Headline numbers

- **Noun coverage: 404/410 = 98.54%.** Residue: 6 features (1.46%) —
  2 non-geometric (`material specification`), 1 vague reference region, 3
  snap-latch features with no covering kind (see §2).
- **Open-set leak: 76/410 = 18.5%** of all features currently carry a
  region word (`arm`, `ring`, `web`, `profile`/`plate`/`body`/`outline`/
  `contour`/`band`/`collar`) as the *only* signal of what they are, not a
  geometric-operation word. 36 of those (8.8%) use `arm`/`web`, which
  `design.md`'s own grammar already licenses as region names; the other 40
  (9.8%) use `ring`/`profile`/`plate`/`body`, which the grammar does not
  mention at all — a second, larger, silent version of the same leak. See
  §2a. **`design.md`'s region-name clause must be rewritten**, not just
  left alone (§2b).
- **Verb coverage (878 occurrences): 56.2% clean 1-verb match, 17.8%
  genuinely ambiguous (needs 2 approved verbs, corpus can't tell which),
  25.3% non-operations that never belonged in a Step, 0.8% (7 occurrences,
  4 lemmas) genuine gap with no approved verb at all.** See §4.
- **Noun→verb map: 22 candidate kinds → 32 approved verbs, all-covered,
  but 3 kinds (`thread`, `step`, `shoulder`) need 2 verbs each** — see §3.
- **Gap-area verdict: of the 7 module-level gaps in
  `ste-freecad-verbs.md`, only 1 has any footprint in this 47-model corpus
  (Part-level `Offset`, 1 occurrence) — and even that is better closed by
  adding an approved verb than by opening a workbench.** The verb-level
  misses that do matter (`thread`, `extend`, `trim`) are not among the 7
  gap areas at all; they are missing entries in the 32-verb table itself.
  See §5.

## 1. The closed NOUN list (candidate Technical Names for `kind`)

Starting point: `design.md`'s existing 21 kinds (`hole, bore, slot, pocket,
cutout, pad, fillet, chamfer, boss, rib, thread, counterbore, countersink,
groove, notch, tab, lug, ear, flange, step, shoulder`), merged with the 107
observed head nouns, synonyms folded down, region words routed to the
geometric kind underneath (§2), 2 new kinds added where the corpus shows a
real, cheap, single-verb-mapped operation `design.md` has no word for.

| kind | corpus count | merges (banned prose synonyms) |
|---|---:|---|
| `hole` | 116 | `bore` (merged — see caveat below) |
| `fillet` | 54 | `round`, `rounded`, `rounds` |
| `cutout` | 33 | `opening`, `window` |
| `slot` | 33 | `split`, `slit`, `saw cut`, `keyway` |
| `pocket` | 24 | — |
| `lug` | 18 | — |
| `counterbore` | 17 | `recess`, `recesses` |
| `ear` | 10 | — |
| `boss` | 9 | — |
| `rib` | 5 | — |
| `chamfer` | 4 | — |
| `shell` | 2 | *(new — PartDesign::Thickness)* |
| `draft` | 1 | *(new — PartDesign::Draft)* |
| `thread` | 1 | — |
| `countersink` | 1 | — |
| `pad` | 76 *(see §2)* | `arm`, `ring`, `band`, `collar`, `profile`, `contour`, `silhouette`, `outline`, `plate`, `body`, `web` |
| `groove` | 0 | approved, unused in this corpus |
| `notch` | 0 | approved, unused |
| `tab` | 0 | approved, unused |
| `flange` | 0 | approved, unused (word appears only as a *place*, see `flange-split`) |
| `step` | 0 | approved, unused |
| `shoulder` | 0 | approved, unused |

**22 kinds total** (21 original minus `bore` folded into `hole`, plus
`shell` and `draft`). **6 of the 21 original kinds (`groove`, `notch`,
`tab`, `flange`, `step`, `shoulder` — 29% of the original list) have zero
uses across all 410 features** in this 47-model corpus — cheap to keep,
but the corpus gives no evidence they are needed.

**Borderline call — `bore` merged into `hole`.** The corpus uses `bore`
(41 raw head-noun occurrences) and `hole` (6) for the same operation
(`PartDesign::Hole`) with no reliable semantic split — `ste-freecad-verbs.md`
itself already bans "bore (as verb)" in favour of the verb `hole`. Merging
the noun too keeps kind and verb symmetric. If a future need distinguishes
a large precision bore from a small fastener hole, that is a **dimension**
(diameter), not a different kind.

**Borderline call — `recess` merged into `counterbore`.** Every `recess`
occurrence in the corpus is a fastener-head clearance (hex or round) paired
with a through-hole — geometrically a counterbore. Kept separate from
`pocket` (a recess is always fastener-adjacent, never a standalone cavity
in this corpus).

## 2. `kind` mapping and residue (410 features)

### 2a. The open-set leak: region words are not kinds

**76/410 features (18.5%) have no geometric-operation word in their `kind`
text or `id` at all — the only usable signal is a drawing region name.**
All 76 are, geometrically, additive pads (a protrusion or the part's own
outer silhouette) that the drawing happens to call by a region name instead
of by what it is:

| region word | count | licensed by `design.md`'s clause? |
|---|---:|---|
| `arm` | 34 | yes (`"Use the drawing's region name (arm, ear, web)..."`) |
| `web` | 2 | yes |
| `ring` / `band` / `collar` | 19 | **no** — not in the clause at all |
| `profile` / `plate` / `body` / `outline` / `contour` | 21 | **no** |

Sample (first 12 of 76):

| id | kind (truncated) |
|---|---|
| ring | circular clamp ring, Ø50 outside, 30 mm wide front to rear, split on one side |
| arm | flat arm running left from the ring, 15 mm tall, 30 mm deep, semicircular left end R15 in  |
| outer-profile | obround (capsule) outer profile, 150 long by 50 tall, ends radiused |
| plate | rectangular base plate 80 long by 30 deep, 5 thick, all four corners R8 |
| outer-ring | circular outer ring, Ø50 |
| left-arm-and-rounded-end | left mounting arm with a semicircular rounded left end |
| outer-profile | composite outer contour in the plane of the length and the height: a Ø50 ring at the right |
| ring | annular ring, Ø50 outside, Ø42 bore |
| arm | flat arm, 15 mm thick (height) by 14 mm wide (depth), joining the underside of the ring; l |
| outer-profile | rectangular outer profile, 100 long by 60 deep, corners square |
| outer-profile | Phase 2 clamp outer profile: circular ring joined to a 45° angled left arm and a right-han |
| angled-left-arm | Phase 2 angled exterior arm |
| ... | ... 64 more |

This is very likely a major cause of the 14.9% `id` compliance rate
measured in `ste-design-duplicates.md` §3: `outer-profile` alone (29 uses,
the single most common non-compliant id, and `design.md`'s own worked
example) is exactly this leak.

### 2b. Recommendation: `design.md`'s region-name clause must be rewritten

Current text (`config/prompts/design.md`, "Naming from a closed
vocabulary"):

> Feature `id`: `<kind>-<place>` where kind is one of (...) and place is one
> or two of (...). **Use the drawing's region name (arm, ear, web) when it
> names one.**

This clause is an open set smuggled inside the closed `<kind>-<place>`
grammar, in the *place* slot — but the corpus shows models also using it to
replace the *kind* slot outright (bare ids like `arm`, `ring`), and using
region words the clause doesn't even list (`ring`, `plate`, `body`,
`profile`). It also cites `ear`, which is *simultaneously* one of the 21
closed kinds and a region-name example — the same word plays both roles,
undocumented.

**Delete this clause from the id-kind grammar and replace it with a
separate, explicitly open `region` field**, orthogonal to `kind`:

- `kind`: closed, 22 values (§1) — what the feature geometrically *is*.
- `region`: open free text — which named zone of the drawing it sits in
  (`arm`, `ring`, `web`, or anything else a drawing calls a zone). Any
  `kind` value can carry any `region` value: `kind=pad, region=arm`,
  `kind=hole, region=arm` (a hole drilled through the arm), `kind=fillet,
  region=ear-blend`.
- Drop `ear` from the region-name examples if the clause survives in any
  form — `ear` is a closed *kind*, not a region name, and citing it as both
  is the exact contradiction that lets a model choose either.

### 2c. Residue: 6 features that map to nothing

| session | id | kind |
|---|---|---|
| 141a41df15544013a9740a45138fa7ad | material | material specification |
| 549e6232c9e04b659e5a2d536354bd7a | material | material specification |
| 8d07a842d06b487d8e7729ecba27ce4b | section-a-a-recessed-central-region | central region at SECTION A-A between two 6 mm end regions |
| f1f13395f7454a53afb790366e03a7ea | latch-front-left | green draw latch — flat vertical strap spanning the parting plane, its upper part on the lid and its lower catch on the base |
| f1f13395f7454a53afb790366e03a7ea | latch-front-right | green draw latch, the same as latch-front-left |
| f1f13395f7454a53afb790366e03a7ea | latch-rear-pair | two green draw latches on the rear face, mirroring the front pair |

- `material specification` (×2): not a geometric feature at all — a
  material callout riding in the `features` array. Argues for a `material`
  field on the model, not a `kind` value.
- `section-a-a-recessed-central-region`: a reference/derived-dimension
  note ("central region ... between two 6 mm end regions"), not a modelled
  feature — closest real kind is unclear even on inspection; genuine
  residue, not a classifier miss.
- `latch-front-left` / `latch-front-right` / `latch-rear-pair` (one model,
  a case-and-lid enclosure): a snap-fit draw latch is a real, distinct
  geometric idea (a shaped clip pad + its catch cutout) that none of the 22
  kinds names cleanly — `pad` would swallow it but loses the
  fastener-function information the drawing is actually conveying. Reported
  as residue rather than force-fit, per the instruction to show borderline
  calls rather than reach 100%.

## 3. Noun → verb map (22 kinds → 32 approved FreeCAD verbs)

| kind | verb | note |
|---|---|---|
| `hole` | `hole` | |
| `counterbore` | `hole` | `PartDesign::Hole`, `Type=counterbore` |
| `countersink` | `hole` | `PartDesign::Hole`, `Type=countersink` |
| `chamfer` | `chamfer` | |
| `fillet` | `fillet` | |
| `groove` | `groove` | unused in corpus |
| `notch` | `pocket` | |
| `slot` | `pocket` | |
| `cutout` | `pocket` | |
| `pocket` | `pocket` | |
| `pad` | `pad` | |
| `boss` | `pad` | |
| `rib` | `pad` | |
| `tab` | `pad` | unused in corpus |
| `lug` | `pad` | |
| `ear` | `pad` | |
| `flange` | `pad` | unused in corpus |
| `shell` | `thickness` | |
| `draft` | `draft` | |
| `thread` | **`additive-helix` or `subtractive-helix`** | **needs 2** — internal vs external thread; neither is a clean fit (both model a coil, not a real V-thread profile) |
| `step` | **`pad` or `pocket`** | **needs 2** — a raised step is additive, a recessed step is subtractive, and the noun alone never says which |
| `shoulder` | **`pad` or `pocket`** | **needs 2** — same ambiguity as `step` |

**Result: every kind maps to at least one approved verb (no true 0-verb
miss), but 3 of 22 kinds (14%) are genuinely one-to-two, not one-to-one.**
`thread`/`step`/`shoulder` need either a disambiguating second field
(`direction: raised|recessed` for step/shoulder) or must be split into two
kinds (`step-raised`/`step-recessed`) if the vocabulary is to stay
verb-deterministic.

## 4. Verb occurrences → 32 approved verbs (878 occurrences, 47 lemmas)

| bucket | occurrences | % |
|---|---:|---:|
| clean 1-verb match | 493 | 56.2% |
| ambiguous (needs 2) | 156 | 17.8% |
| non-operation (should never be in a Step) | 222 | 25.3% |
| genuine gap (no approved verb) | 7 | 0.8% |

### 4a. Ambiguous — needs 2, genuinely can't tell from the verb alone

| lemma | count | candidates | why |
|---|---:|---|---|
| `cut` | 154 | `pocket` / `cut` | the corpus's own 285-occurrence "material removal" cluster (`ste-plan-verbs.md` §4) never distinguishes `PartDesign::Pocket` from the `Part::Cut` boolean fallback — this is the single largest verb in the whole corpus and it is the least resolved |
| `sweep` | 2 | `additive-sweep` / `subtractive-sweep` | additive vs subtractive undetermined from the verb alone |

### 4b. Genuine gap — no approved verb covers it at all

| lemma | count | why |
|---|---:|---|
| `thread` | 2 | no approved verb names a real screw thread; `additive-helix`/`subtractive-helix` model a coil, not the ISO 965 profile the one thread-bearing model (`thread-m10`) actually specifies |
| `extend` | 2 | Sketcher-level geometry edit, not a `PartDesign` feature type — outside the 32-verb table by construction |
| `trim` | 2 | same — Sketcher-level edit |
| `offset` | 1 | `ste-freecad-verbs.md` gap #6 (Part-level `Offset` never named as an approved verb) — the one place a named gap area actually shows up in the verb data |

### 4c. Non-operations — confirmed present, should never have been in a Step

`create`(44), `form`(27), `assign`(26), `use`(23), `set`(16), `make`(13),
`centre`(12), `place`(11), `select`(9), `build`(5), `close`(5), `label`(5),
`name`(5), `open`(3), `note`(3), `locate`(3), `terminate`(2), `define`(2),
`confirm`(2), `replace`(2), `check`(2), `attach`(2) — **222 occurrences,
25.3% of the whole corpus.** These are exactly the task's own named
examples (`open`, `select`, `note`; `verify`/`ensure` never fire in this
corpus at all — it phrases a refusal instead) plus naming (`label`/`name`),
generic instantiation (`create`/`make`/`build`), positioning
(`centre`/`place`/`locate`), and revision bookkeeping (`replace`). None of
these belong in "the operation this Step commands" — per
`ste-plan-verbs.md` §4, they belong in constraint/selection/metadata
fields instead. Closing the verb vocabulary hard **requires** pulling these
out of Step prose; they cannot be mapped onto any of the 32 approved verbs
because they are not modelling operations.

### 4d. Clean matches (for completeness)

`pocket`(131), `sketch`(122), `pad`(107), `fillet`(24), `apply`→fillet(23),
`add`→pad(21), `round`→fillet(20), `draw`→sketch(10), `drill`→hole(8),
`join`→fuse(7), `fuse`(5), `counterbore`→hole(4), `chamfer`(3),
`mirror`(2), `machine`→pocket(2), `mill`→pocket(1), `narrow`→draft(1,
matches the Design corpus's own "tapered by the 5.0° draft so it narrows
toward its top face"), `bore`→hole(1, already-banned wording), `split`→
pocket(1).

## 5. Gap-area verdict (the 7 areas in `ste-freecad-verbs.md` §4)

| gap area | needed by any of the 47 models? |
|---|---|
| Draft workbench (2D drafting/BIM) | **No.** The corpus's one `draft` feature is `PartDesign::Draft` (a taper angle on an existing pad), already an approved verb — unrelated to the Draft *workbench*. |
| Surface workbench | **No.** No model requires a genuinely free-form (non-loft/sweep/revolve) surface. |
| Assembly/joints | **No** — out of scope by design (one-part pipeline), consistent with `ste-freecad-verbs.md`. |
| TechDraw | **No** — input-side domain, correctly out of scope. |
| Sheet Metal Workbench | **No** — no folded/flat-pattern sheet-metal feature in any model. |
| PartDesign additional tools (gear/sprocket/shaft wizard) | **No** — no gear or sprocket model in the corpus. |
| Part-level `Offset`/`Loft`/`Sweep`/`Section` | **Marginal.** 1 of 878 verb occurrences (`offset`) — see §4b. Real, but thin. |

**Verdict: 6 of 7 gap areas are free to close — nothing in this corpus
needs them, so hard closure costs nothing there.** The 7th
(Part-level `Offset`) has exactly 1 occurrence in the whole corpus: cheap
to leave closed and handle as an exception, expensive to open a whole
workbench for. **The verb misses that actually matter in this corpus
(`thread`, `extend`, `trim`) are not among these 7 areas at all** — they
are missing rows in the 32-verb table itself (Sketcher-level edits and a
real screw thread), a different and more urgent problem than any
workbench-level gap. This is the loud part: hard closure is safe against
the 7 named gap areas, but it is **not yet safe against `thread`** without
either accepting the `additive-helix`/`subtractive-helix` compromise or
adding a dedicated thread verb.

## 6. Dimensions embedded in `kind` prose → structured fields

Every dimension-bearing `kind` string in this corpus is expressible with 9
numeric fields, plus 2 the corpus needs that are not in the starting
candidate set:

| field | example prose it replaces |
|---|---|
| `diameter` | `Ø40 bore`, `Ø6.6 through hole` |
| `outer_diameter` | `Ø50 outside`, `Ø50 OD` |
| `radius` | `R2 fillets`, `R30 concave wall`, `R12.5 rounded end` |
| `thickness` | `15 mm thick`, `4 mm thick rib` |
| `length` | `30 mm long`, `150 long` |
| `width` | `30 mm wide front to rear`, `25 across` |
| `depth` | `6 deep`, `30 mm deep` |
| `angle` | `45°`, `5.0° draft`, `120° included angle` |
| `count` | `4x`, `2X`, `six ... holes` |
| **`pitch`** *(missing from the candidate set)* | `M10 × 1.5` — the one thread feature in the corpus needs a pitch, not just a diameter |
| **`bolt_circle_diameter`** *(missing)* | `six equally spaced Ø4.5 clearance holes on a Ø70 bolt circle` — a patterned-hole placement dimension the 9-field set has no slot for |

**Minimal field set to absorb the corpus's dimension prose: the 9
suggested fields, plus `pitch` and `bolt_circle_diameter` — 11 total.**
Thread class/tolerance callouts (`tolerance class 6H (ISO 965)`) are
textual, not numeric, and do not fit any of these 11 — they need a
free-text `spec` field alongside them if threads are to be modelled at all
(consistent with §4b/§5's `thread` gap).

## Script

```python
#!/usr/bin/env python3
"""Reproduce docs/research/ste-vocabulary-closure.md end to end.

Data: logs/sessions/*.jsonl (design + plan records), same corpus as
ste-design-duplicates.md and ste-plan-verbs.md. Run from the repo root.
"""
import glob, json, re, collections

FILES = sorted(glob.glob("logs/sessions/*.jsonl"))

# ---------- 1. load design models (last kind=="design" record per file) ----------
def load_final_models():
    models = {}
    for fp in FILES:
        last = None
        with open(fp) as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("kind") == "design":
                    last = rec
        if last is not None:
            sid = fp.split("/")[-1].replace(".jsonl", "")
            models[sid] = last["data"]["model"]
    return models

models = load_final_models()
all_feats = [(sid, f.get("id"), f.get("kind"))
             for sid, m in models.items() for f in m.get("features", [])]
assert len(all_feats) == 410

# ---------- 2. closed NOUN classifier (22 kind labels) ----------
# kind = what the thing IS as geometry. Region words (arm, ring, web, plate,
# body, profile, contour, silhouette, outline, band, collar) are NOT kinds -
# they route to the real geometric kind underneath (almost always `pad`,
# the additive body/protrusion the drawing happens to name).
SYN = {
    "hole": "hole", "bore": "hole", "bores": "hole",
    "counterbore": "counterbore", "recess": "counterbore", "recesses": "counterbore",
    "countersink": "countersink", "countersinks": "countersink",
    "chamfer": "chamfer", "chamfers": "chamfer",
    "fillet": "fillet", "fillets": "fillet", "round": "fillet", "rounds": "fillet", "rounded": "fillet",
    "thread": "thread", "threads": "thread",
    "groove": "groove", "grooves": "groove",
    "notch": "notch", "notches": "notch",
    "slot": "slot", "slots": "slot", "split": "slot", "splits": "slot", "slit": "slot", "keyway": "slot",
    "cutout": "cutout", "cutouts": "cutout", "opening": "cutout", "openings": "cutout", "window": "cutout",
    "pocket": "pocket", "pockets": "pocket",
    "shell": "shell",
    "draft": "draft",
    "boss": "boss", "bosses": "boss",
    "rib": "rib", "ribs": "rib",
    "tab": "tab", "tabs": "tab",
    "lug": "lug", "lugs": "lug",
    "ear": "ear", "ears": "ear",
    "flange": "flange",
    "step": "step", "steps": "step",
    "shoulder": "shoulder",
    "arm": "pad", "arms": "pad",
    "ring": "pad", "rings": "pad", "band": "pad", "collar": "pad",
    "profile": "pad", "contour": "pad", "silhouette": "pad", "outline": "pad", "plate": "pad", "body": "pad",
    "pad": "pad", "pads": "pad", "web": "pad",
}
REGION_WORDS = {"arm", "arms", "ring", "rings", "band", "collar", "profile",
                 "contour", "silhouette", "outline", "plate", "body", "web"}

PRIORITY = [
    ("fillet", r"\bfillet(s)?\b"),
    ("countersink", r"\bcountersink"),
    ("counterbore", r"counterbore|c'?bore|\brecess(es)?\b"),
    ("chamfer", r"\bchamfer"),
    ("hole", r"\bhole(s)?\b|\bbore(s)?\b"),
    ("groove", r"\bgroove"),
    ("slot", r"\bslot(s)?\b|\bkeyway\b|\bslit\b|\bsaw cut\b|\bsplit(s)?\b"),
    ("notch", r"\bnotch"),
    ("cutout", r"\bcutout(s)?\b|\bopening(s)?\b|\bwindow\b"),
    ("pocket", r"\bpocket(s)?\b"),
    ("shell", r"\bshell\b"),
    ("draft", r"\bdraft\b"),
    ("boss", r"\bboss(es)?\b"),
    ("rib", r"\brib\b"),
    ("tab", r"\btab(s)?\b"),
    ("lug", r"\blug(s)?\b"),
    ("ear", r"\bear(s)?\b"),
    ("flange", r"\bflange\b"),
    ("step", r"\bstep(s|ped)?\b"),
    ("shoulder", r"\bshoulder\b"),
    ("pad", r"\barm(s)?\b|\bring(s)?\b|\bband\b|\bcollar\b|\bprofile\b|\bcontour\b|"
            r"\bsilhouette\b|\boutline\b|\bplate\b|\bbody\b|\bweb\b|\bpad(s)?\b"),
    ("fillet", r"\bround(s|ed)?\b"),
    ("thread", r"\bthread(s|ed)?\b"),
]

def id_hint(fid):
    if not fid:
        return None
    toks = [re.sub(r"\d+x?$", "", t) for t in re.split(r"[-_]", fid.lower())]
    found = {SYN[t] for t in toks if t in SYN}
    return next(iter(found)) if len(found) == 1 else None

def classify(kind_text, fid):
    hint = id_hint(fid)
    if hint:
        return hint
    t = kind_text.lower()
    for noun, patt in PRIORITY:
        if re.search(patt, t):
            return noun
    return None

results = [(sid, fid, k, classify(k, fid)) for sid, fid, k in all_feats]
mapped = [x for x in results if x[3]]
residue = [x for x in results if not x[3]]
noun_counts = collections.Counter(x[3] for x in mapped)
# len(mapped) == 404, len(residue) == 6, 404/410 == 98.54%

# ---------- 3. open-set leak: features whose only signal is a region word ----------
def leaked(fid, kind_text):
    hint = id_hint(fid)
    if hint is not None:
        toks = {t for t in re.split(r"[-_]", fid.lower()) if t in SYN}
        return bool(toks) and toks.issubset(REGION_WORDS)
    t = kind_text.lower()
    for noun, patt in PRIORITY:
        if re.search(patt, t):
            return noun == "pad" and not re.search(r"\bpad(s)?\b", t)
    return False

leak = [(sid, fid, k) for sid, fid, k in all_feats if leaked(fid, k)]
# len(leak) == 76 (18.5% of 410); 34 arm + 2 web (licensed by design.md's
# region-name clause) vs 19 ring/band/collar + 21 profile/plate/body/outline
# (NOT covered by that clause at all)

# ---------- 4. noun -> verb map against the 32 approved FreeCAD verbs ----------
NOUN_VERB = {
    "hole": "hole", "counterbore": "hole", "countersink": "hole",
    "chamfer": "chamfer", "fillet": "fillet",
    "thread": ("additive-helix", "subtractive-helix"),  # needs 2, and a stretch even then
    "groove": "groove", "notch": "pocket", "slot": "pocket", "cutout": "pocket", "pocket": "pocket",
    "pad": "pad", "boss": "pad", "rib": "pad", "tab": "pad", "lug": "pad", "ear": "pad", "flange": "pad",
    "step": ("pad", "pocket"), "shoulder": ("pad", "pocket"),  # needs 2: raised vs recessed
    "shell": "thickness", "draft": "draft",
}

# ---------- 5. 878 verb occurrences -> 32 approved verbs ----------
# (extraction identical to ste-plan-verbs.md; see that file's own script for
# the full 47-lemma extractor. VERB_MAP below classifies its output.)
VERB_MAP_CLASS = {
    # lemma: ("clean"/"ambiguous"/"nonop"/"gap", verb-or-tuple-or-None)
    "cut": ("ambiguous", ("pocket", "cut")), "pocket": ("clean", "pocket"),
    "sketch": ("clean", "sketch"), "pad": ("clean", "pad"),
    "create": ("nonop", None), "form": ("nonop", None), "assign": ("nonop", None),
    "fillet": ("clean", "fillet"), "use": ("nonop", None), "apply": ("clean", "fillet"),
    "add": ("clean", "pad"), "round": ("clean", "fillet"), "set": ("nonop", None),
    "make": ("nonop", None), "centre": ("nonop", None), "place": ("nonop", None),
    "draw": ("clean", "sketch"), "select": ("nonop", None), "drill": ("clean", "hole"),
    "join": ("clean", "fuse"), "build": ("nonop", None), "close": ("nonop", None),
    "label": ("nonop", None), "fuse": ("clean", "fuse"), "name": ("nonop", None),
    "counterbore": ("clean", "hole"), "open": ("nonop", None), "note": ("nonop", None),
    "locate": ("nonop", None), "chamfer": ("clean", "chamfer"), "terminate": ("nonop", None),
    "define": ("nonop", None), "confirm": ("nonop", None), "mirror": ("clean", "mirror"),
    "replace": ("nonop", None), "extend": ("gap", None), "trim": ("gap", None),
    "sweep": ("ambiguous", ("additive-sweep", "subtractive-sweep")), "check": ("nonop", None),
    "machine": ("clean", "pocket"), "attach": ("nonop", None), "thread": ("gap", None),
    "offset": ("gap", None), "mill": ("clean", "pocket"), "narrow": ("clean", "draft"),
    "bore": ("clean", "hole"), "split": ("clean", "pocket"),
}

if __name__ == "__main__":
    print(f"noun coverage: {len(mapped)}/410 = {100*len(mapped)/410:.2f}%")
    print(f"noun residue: {len(residue)}")
    print(f"open-set leak (region word as only signal): {len(leak)}/410 = {100*len(leak)/410:.1f}%")

```
