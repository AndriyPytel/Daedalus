# Design-phase duplicate-feature defect — measurement

Source: `logs/sessions/*.jsonl` (456 files), records with `kind=="design"`.
Grammar/vocabulary reference: `config/prompts/design.md`.

**Method note.** 47 of 456 session files contain at least one `kind=="design"`
record (67 records total; 30 files have 1 record, 14 have 2, 3 have 3).
Verified across all 17 multi-record sessions that every `design` record is a
FULL model snapshot — same key set (`conflicts`/`datums`/`envelope`/`features`/
`not_defined`/`printed_on_the_sheet`), full feature list each time — never a
sparse fragment. One session (`247c2aaed...`) shows values legitimately
changing between records; the model's own `was`/`because` fields show this is
design.md's documented revision-override behaviour (a later drawing page
superseding an earlier feature's datum), not truncation. **The LAST
`design` record per session file is therefore used as that session's final
model** — no field-by-field merge across records was needed or performed.

## 1. Models and feature counts

- Design models found: **47**
- Features per model: min **1**, median **9**, max **14**
- Total features across all models: **410**

## 2. `kind` is free prose

- Word-count distribution over all 410 `kind` strings: min 2, median **10**,
  max 67 words.
- Bare noun phrase (≤3 words): **55** (13.4%)
- Full sentence / clause (>3 words): **355** (86.6%)
- Distinct `kind` strings: **355** (out of 410 features — almost every
  feature's `kind` is unique text, even before counting near-duplicates)
- Distinct head nouns (last content word of `kind`, stopwords/numbers
  stripped): **107**

**The gap: 355 distinct strings collapse to 107 distinct head nouns** — a
>3x reduction. The same physical concept ("bore", 41 occurrences; "ring", 41;
"pocket", 8; "hole", 6) is written a different way essentially every time a
feature is described, which is exactly the condition under which a
downstream reader (or an automated de-duplicator) cannot tell two features
apart by text alone, and exactly the condition under which two independently
worded descriptions of the same physical thing pass as different features.
(Caveat: the head-noun extraction is a naive last-content-word heuristic — it
also produces noise entries like "red", "r", "b", "shcs" from prose that
doesn't end in a true noun. It undercounts the true concept-collapse rate if
anything, since some of those noise entries are themselves fragments of a
real head noun like "fillet"/"fillets".)

## 3. `id` grammar compliance

design.md mandates `<kind>-<place>` from closed lists (21 kinds, 11 places,
plus a documented `-4x` repeat suffix and a documented exception for
drawing-named regions like `arm`/`ear`/`web`).

- Strictly compliant (`kind` token in the closed kind list, every remaining
  hyphen-token in the closed place list): **61 / 410 = 14.9%**
- Non-compliant: **349 / 410 = 85.1%**

Top non-compliant ids (count = number of features across all models using
that exact id string):

| id | count |
|---|---|
| outer-profile | 29 |
| red-edge-fillets | 10 |
| fillets-r2 | 9 |
| ring-bore | 9 |
| right-mounting-hole | 9 |
| ring-split | 8 |
| left-mounting-hole | 7 |
| ring | 6 |
| edge-fillets | 6 |
| arm | 5 |
| cutout-web | 5 |
| split-gap | 5 |
| rib | 4 |
| fillets | 4 |
| slot-arm | 4 |
| web-cutout | 4 |
| arm-cutout | 4 |
| fillets-red | 4 |
| bore-ring | 3 |
| outer-ring | 3 |

Note: `outer-profile`, the single most common non-compliant id (29 uses), is
the id design.md's own worked example uses for the outer-contour feature —
the doc's own example is not covered by the closed kind list it states.

## 4. Likely duplicate pairs

Two detectors were run inside each model, both excluding legitimate
mirror/repeat pairs (ids or position datums naming opposite sides —
left/right, front/rear, upper/lower, top/bottom, inner/outer — which are
overwhelmingly genuine distinct features, e.g. `bore-left`/`bore-right` at
different positions) and excluding the generic `fillet`/`material` kind
family (an edge-fillet or material-spec feature legitimately has no single
`position`, so an empty `position` there is not the defect signature):

1. **Empty `position` vs. best-matching filled feature** in the same model —
   design.md names an empty `position` explicitly as "the shape this defect
   arrives in."
2. **Near-identical `kind` text (similarity ≥ 0.85)** between two filled
   features in the same model, after the mirror-pair exclusion.

After manually checking every candidate's actual `position` values (most
"identical `kind`" hits, e.g. `bore-right`/`bore-central-vertical` at Ø40 vs
Ø35, or `ring-od`/`ring-bore` — an OD surface and a bore are two legitimate
features of one ring, not a duplicate — turned out to be distinct real
features once positions were compared), **4 convincing duplicate pairs
survive, in 4 of the 47 models (8.5%)**:

### `e76b61d5bc7143699b451549c90433f7` — `slot-arm` / `cutout-web`
This is design.md's own cited case study (the 2023 pipe clamp). `cutout-web`
carries an empty `position` and describes "curved cutout in the web between
the top of the arm and the ring"; `slot-arm` (filled position, same
`seen_in` view) is "slot through the 15 mm arm, 30 mm long ... ending at the
vertical plane through the ring axis" — the same cavity in the web, drawn
once in plan and once in VIEW B, written up as two features.

### `6d67ee7f5fa4482ebb49495c2f6aef5c` — `slot` / `cutout-blend`
The same defect pattern, independently, in a different run. `cutout-blend`
(empty position): "curved cutout blending the top of the 15 mm arm up into
the ring." `slot` (filled): "rectangular slot through the arm, 30 mm long,
with rounded/filleted ends per VIEW B and the plan." Same arm/ring junction
cavity, two ids.

### `51d7a2f1...` — `bore-upper` / `hole-boss`
Both carry the identical `kind` string `"Ø15 through hole in the upper
boss"`, identical `seen_in` (`["top view", "SECTION B-B"]`), identical
diameter, and both are described as coaxial with the same boss —
`bore-upper` measured from `the front-to-rear mid-plane` + `the left end
tangent plane`, `hole-boss` measured from `the boss axis` directly. Two ids
for arithmetically the same hole, positioned from two different datum
framings.

### `549e6232c9e04b659e5a2d536354bd7a` — `left-mounting-hole` / `phase2-left-mounting-slot`
Page-1 feature `left-mounting-hole` ("Ø6.6 through mounting hole with Ø11
counterbore") vs. page-2 feature `phase2-left-mounting-slot` ("Ø6.6 by 8.0
obround through slot with Ø11 counterbore slot") — same mounting location,
same fastener clearance and counterbore diameter, the page-2 revision turning
a round hole into an obround slot. `phase2-left-mounting-slot` has an empty
`position` (its place is the one already given to `left-mounting-hole`) and
carries no `superseded_by` back-reference, so both stand as live features of
the part.

## 5. `superseded_by` usage

`superseded_by` appears on **5 features total**, across 3 of the 47 models
(`247c2aaed...`, `b70b6931...`, `fe663c12...`):

| session | feature | superseded_by |
|---|---|---|
| 247c2aaed0734866a8aa9d169f7303bd | fillet-end-left | outer-profile |
| b70b6931ea9e4434bb2ba37e23c6222f | hole-left-ear | arm-45 |
| b70b6931ea9e4434bb2ba37e23c6222f | arm-body-phase1 | arm-45 |
| fe663c12890b4d4497ad458136dfa880 | left-arm | angled-left-arm |
| fe663c12890b4d4497ad458136dfa880 | left-lug-hole | left-arm-slot |
| **fe663c12890b4d4497ad458136dfa880** | left-lug-hole | left-arm-slot |

**None of the 5 `superseded_by` links correspond to any of the 4 duplicate
pairs found in §4** — all 5 are revision links between a page-1 feature and
its page-2 replacement on a *different* feature than the ones flagged as
duplicates. The mechanism exists (it fires 5 times) but it never fires on the
cases that are actually duplicates: the merge machinery and the defect it
exists to catch do not currently intersect in this corpus.

## 6. Datum and envelope closed-vocabulary compliance

design.md closes the vocabulary for datum names, feature `position` keys
(which must be one of the datum names), and `envelope` keys.

| field | total | compliant | rate |
|---|---|---|---|
| `datums[].name` | 329 | 155 | **47.1%** |
| feature `position` keys | 872 | 414 | **47.5%** |
| `envelope` keys | 148 | 38 | **25.7%** |

Top non-compliant datum names (also the dominant non-compliant `position`
keys):

| datum name | count |
|---|---|
| the left end tangent plane | 11 |
| the right end tangent plane | 5 |
| the left bore axis | 5 |
| the right bore axis | 5 |
| the front-to-rear centre plane | 5 |
| the bottom tangent plane of the ring | 5 |
| the plate top face | 3 |
| the boss axis | 3 |
| the left end | 3 |
| the mid-plane of the depth | 3 |
| the depth centre plane | 2 |
| the mid-depth centre plane | 2 |
| the leftmost tangent plane of the left rounded end | 2 |
| the bottom face of the arm | 2 |
| the centre plane of the depth | 2 |

Top non-compliant envelope keys:

| envelope key | count |
|---|---|
| height between the top and bottom faces | 10 |
| length between the end faces | 6 |
| depth between the front and rear faces | 6 |
| length between the end tangent planes | 5 |
| depth front to rear between the front and rear faces | 4 |
| length between the leftmost and rightmost tangent planes | 4 |
| length between the left and right end tangent planes | 2 |
| height from the bottom face of the arm to the top of the ring | 2 |
| length between the left end face and the right end face | 2 |
| depth front to rear between the front face and the rear face | 2 |
| height between the bottom face and the top face | 2 |

A closed list obeyed roughly 1 time in 2 (datums, position keys) or roughly 1
time in 4 (envelope) is itself a finding: features do not merge in part
because the model has no consistent vocabulary of *places* to compare them
against — `the left end tangent plane` and `the left end face` may or may not
name the same face, and nothing in the data enforces that they do.

## Script

```python
#!/usr/bin/env python3
"""Measure the duplicate-feature defect in Design-phase models.

Data: logs/sessions/*.jsonl, records with kind=="design".
A session can log more than one kind=="design" record (design.md's "the model
only ever grows" / revision-override mechanism). Verified across all 17
multi-record sessions that every record is a FULL model snapshot (same key
set, same-size feature list), not a sparse fragment - so the LAST design
record per session file is taken as that session's final model. Where values
legitimately change between records (e.g. session 247c2aaed...), the model's
own 'was'/'because' bookkeeping shows this is the documented revision-override
behaviour (a later drawing page superseding an earlier value), not truncation.
"""
import json, re, glob, difflib, collections, statistics

FILES = sorted(glob.glob("logs/sessions/*.jsonl"))

KINDS = {"hole","bore","slot","pocket","cutout","pad","fillet","chamfer","boss","rib",
         "thread","counterbore","countersink","groove","notch","tab","lug","ear","flange",
         "step","shoulder"}
PLACES = {"left","right","front","rear","top","bottom","upper","lower","centre","inner","outer"}
DATUMS_CLOSED = {
 "the left end face","the right end face","the front face","the rear face",
 "the top face","the bottom face","the ring axis","the bore axis",
 "the centre plane","the longitudinal centre plane","the transverse centre plane"
}
ENVELOPE_CLOSED = {"length","width","height","depth front to rear","depth left to right","thickness"}
STOPWORDS = {"a","an","the","of","with","in","on","at","for","to","and","through","into"}
ID_PATTERN = re.compile(r'^([a-z]+)-(.+)$')
SIDE_WORDS = ["left","right","front","rear","upper","lower","top","bottom","inner","outer"]
CONFLICTING_PAIRS = {("left","right"), ("front","rear"), ("upper","lower"), ("top","bottom"), ("inner","outer")}
EXCLUDE_TOKENS = ["fillet", "material"]


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


def head_noun(kind):
    s = re.sub(r'\([^)]*\)', '', kind.lower())
    tokens = [t for t in re.findall(r"[a-z']+", s) if t not in STOPWORDS]
    tokens = [t for t in tokens if not any(c.isdigit() for c in t)]
    return tokens[-1] if tokens else None


def id_compliant(fid):
    mo = ID_PATTERN.match(fid)
    if not mo:
        return False
    kind_tok, rest = mo.group(1), mo.group(2).split("-")
    if rest and re.match(r'^\d+x$', rest[-1]):
        rest = rest[:-1]
    return kind_tok in KINDS and rest and all(t in PLACES for t in rest)


def side_markers(pos):
    keys = " ".join((pos or {}).keys()).lower()
    return {w for w in SIDE_WORDS if w in keys}


def id_side_markers(fid):
    fid = fid.lower()
    return {w for w in SIDE_WORDS if w in fid}


def looks_like_mirror(fi, fj):
    si_id, sj_id = id_side_markers(fi["id"]), id_side_markers(fj["id"])
    for a, b in CONFLICTING_PAIRS:
        if (a in si_id and b in sj_id) or (b in si_id and a in sj_id):
            return True
    si_pos, sj_pos = side_markers(fi.get("position")), side_markers(fj.get("position"))
    for a, b in CONFLICTING_PAIRS:
        if (a in si_pos and b in sj_pos) or (b in si_pos and a in sj_pos):
            return True
    return False


def excluded_kind(k):
    kl = k.lower()
    return any(t in kl for t in EXCLUDE_TOKENS)


def main():
    models = load_final_models()
    feat_counts = sorted(len(m.get("features", [])) for m in models.values())

    all_feats = [(sid, f) for sid, m in models.items() for f in m.get("features", [])]
    word_counts = [len(f.get("kind", "").split()) for _, f in all_feats if f.get("kind")]
    kind_strings = {f.get("kind") for _, f in all_feats if f.get("kind")}
    head_nouns = collections.Counter(head_noun(f.get("kind", "")) for _, f in all_feats if f.get("kind"))

    total_ids = sum(1 for _, f in all_feats if f.get("id"))
    compliant_ids = sum(1 for _, f in all_feats if f.get("id") and id_compliant(f["id"]))
    noncompliant_ids = collections.Counter(f["id"] for _, f in all_feats if f.get("id") and not id_compliant(f["id"]))

    total_datums = sum(len(m.get("datums", [])) for m in models.values())
    compliant_datums = sum(1 for m in models.values() for d in m.get("datums", []) if d.get("name") in DATUMS_CLOSED)
    noncompliant_datums = collections.Counter(
        d.get("name") for m in models.values() for d in m.get("datums", []) if d.get("name") not in DATUMS_CLOSED)

    total_env = sum(len(m.get("envelope", {})) for m in models.values())
    compliant_env = sum(1 for m in models.values() for k in m.get("envelope", {}) if k in ENVELOPE_CLOSED)
    noncompliant_env = collections.Counter(
        k for m in models.values() for k in m.get("envelope", {}) if k not in ENVELOPE_CLOSED)

    # duplicate pairs among filled positions: text-similarity, mirror/fillet-excluded
    filled_candidates = []
    for sid, m in models.items():
        feats = m.get("features", [])
        for i in range(len(feats)):
            for j in range(i + 1, len(feats)):
                fi, fj = feats[i], feats[j]
                ki, kj = fi.get("kind", ""), fj.get("kind", "")
                if not ki or not kj or looks_like_mirror(fi, fj) or excluded_kind(ki) or excluded_kind(kj):
                    continue
                ratio = difflib.SequenceMatcher(None, ki.lower(), kj.lower()).ratio()
                if ratio >= 0.85:
                    filled_candidates.append((ratio, sid, fi, fj))

    # duplicate pairs from empty position vs best filled-position match
    empty_candidates = []
    for sid, m in models.items():
        feats = m.get("features", [])
        empties = [f for f in feats if not f.get("position") and not excluded_kind(f.get("kind", ""))]
        others = [f for f in feats if f.get("position")]
        for ef in empties:
            best = None
            for of in others:
                ratio = difflib.SequenceMatcher(None, ef.get("kind", "").lower(), of.get("kind", "").lower()).ratio()
                overlap = len(set(ef.get("seen_in") or []) & set(of.get("seen_in") or []))
                score = ratio + 0.1 * overlap
                if best is None or score > best[0]:
                    best = (score, ratio, overlap, of)
            if best:
                empty_candidates.append((best[0], sid, ef, best[3]))

    superseded = [(sid, f["id"], f["superseded_by"])
                  for sid, m in models.items() for f in m.get("features", []) if f.get("superseded_by")]

    return dict(models=models, feat_counts=feat_counts, word_counts=word_counts,
                kind_strings=kind_strings, head_nouns=head_nouns,
                total_ids=total_ids, compliant_ids=compliant_ids, noncompliant_ids=noncompliant_ids,
                total_datums=total_datums, compliant_datums=compliant_datums, noncompliant_datums=noncompliant_datums,
                total_env=total_env, compliant_env=compliant_env, noncompliant_env=noncompliant_env,
                filled_candidates=filled_candidates, empty_candidates=empty_candidates, superseded=superseded)


if __name__ == "__main__":
    r = main()
    print("models:", len(r["models"]))
    print("id compliance: %d/%d = %.1f%%" % (r["compliant_ids"], r["total_ids"], 100 * r["compliant_ids"] / r["total_ids"]))

```
