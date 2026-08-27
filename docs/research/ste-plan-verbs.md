# Plan-phase Step verbs — measured open verb set

Source: `logs/sessions/*.jsonl` (456 files), records with `kind=="plan"`,
field `data["steps"]` (list of prose Step strings). Script at the end
reproduces every number below.

## Headline numbers

- **584** Step strings collected across 456 session files.
- Of those, **167 (29%)** are Ukrainian-language plans (`kind=="plan"` runs
  in a different locale) and **3** are `"No document change is possible"` /
  `"Cannot ..."` refusal Steps with no real operation. Both are excluded
  from the verb count below — an ASD-STE100 verb list is an English
  controlled-language artifact, and a refusal Step commands nothing (one
  refusal, "Cannot add the requested fillet ...", would otherwise be
  miscounted as an `add` operation by a naive scan).
- **414** English, operation-bearing Steps remain. The extractor found at
  least one operation verb in **all 414** of them (0 misses) — recall
  against the English subset is complete by this measure.
- **878** verb occurrences extracted from those 414 Steps.
- **47 distinct verb lemmas.**
- **18** verbs cover 90% of occurrences; **41** verbs cover 99%.
- **5** verbs occur exactly once each (the long tail: `offset`, `mill`,
  `narrow`, `bore`, `split`).
- **253 of 414 (61%)** English Steps contain more than one detected
  operation verb — "one Step = one operation" is violated in a majority of
  Steps as currently phrased. Maximum found in a single Step: **7**.
- **9 synonym clusters** identified (see §4) accounting for **626 of 878**
  occurrences (71%) — most of the "open" set is really a small closed set
  wearing different names.

## 1. Extraction method (what counts as an "operation verb")

A Step string is prose, not structured data, and the same word (`pocket`,
`bore`, `centre`, `open`, `form`, `name`, ...) is used in this corpus both
as a verb ("**pocket** a Ø42 circle") and as a noun/adjective ("a Ø42
**pocket**", "the **bore** axis"). A plain word-frequency count conflates
the two and overstates the operation-verb signal.

The script instead:

1. Strips the trailing `(satisfies: ...)` traceability block from each
   Step (present in 382/584 Steps) — that block repeats domain nouns like
   `position`/`axis` that otherwise pollute the count.
2. Drops the 167 Ukrainian-language Steps and the 3 refusal Steps.
3. Splits each remaining Step into clauses on `. ; : — and then`.
4. For a curated list of 85 candidate CAD/instruction verb stems (with
   English inflections `s/ed/ing`), counts an occurrence only when the verb
   form is **clause-initial**, OR is immediately followed by a
   determiner/pronoun/number (`the/a/an/it/its/both/all/two/...`), OR is
   immediately followed by a capitalised proper noun (a named FreeCAD
   object, e.g. `Create a Sketcher::SketchObject named HexProfile`).
5. `centre`/`center` spelling variants are merged into one lemma.

This is a heuristic, not a parser — it was spot-checked verb-by-verb
against sampled contexts and tuned until every top-30 lemma showed
clearly majority-verb usage in its samples. The one lemma where the
heuristic could not be made precise (`lower`, 100% adjectival — "lower
wire", "lower boundaries" — never a command in this corpus) was removed
from the candidate list rather than reported as a false verb.

## 2. Full frequency table (47 lemmas, 878 occurrences)

| rank | verb | count | rank | verb | count |
|---|------|-------|---|------|-------|
| 1 | cut | 154 | 25 | note | 3 |
| 2 | pocket | 131 | 26 | locate | 3 |
| 3 | sketch | 122 | 27 | chamfer | 3 |
| 4 | pad | 107 | 28 | terminate | 2 |
| 5 | create | 44 | 29 | define | 2 |
| 6 | form | 27 | 30 | confirm | 2 |
| 7 | assign | 26 | 31 | mirror | 2 |
| 8 | fillet | 24 | 32 | replace | 2 |
| 9 | use | 23 | 33 | extend | 2 |
| 10 | apply | 23 | 34 | trim | 2 |
| 11 | add | 21 | 35 | sweep | 2 |
| 12 | round | 20 | 36 | check | 2 |
| 13 | set | 16 | 37 | machine | 2 |
| 14 | make | 13 | 38 | attach | 2 |
| 15 | centre | 12 | 39 | thread | 2 |
| 16 | place | 11 | 40 | offset | 1 |
| 17 | draw | 10 | 41 | mill | 1 |
| 18 | select | 9 | 42 | narrow | 1 |
| 19 | drill | 8 | 43 | bore | 1 |
| 20 | join | 7 | 44 | split | 1 |
| 21 | build | 5 | | | |
| 22 | close | 5 | | | |
| 23 | label | 5 | | | |
| 24 | fuse | 5 | | | |
| — | name | 5 | | | |
| — | counterbore | 4 | | | |
| — | open | 3 | | | |

(47 rows total — three ties left the running rank number ambiguous, so
`name`, `counterbore`, `open` are listed without a rank column; the exact
sorted list is what the script's `sorted_verbs` prints.)

**Coverage curve**: 18 verbs (`cut` through `select`) account for 90% of
all occurrences; the remaining 29 verbs — 62% of the distinct vocabulary —
share the last 10%. 5 verbs are singletons. This tail shape (a handful of
high-frequency terms + a long thin tail of one-off phrasing) is what a
closed ASD-STE100-style list would need to absorb by merging synonyms, not
by keeping every observed spelling.

**Verbs in the candidate list that never fired** (defined, spot-checked,
zero matches in the corpus — a genuine absence, not a bug): `extrude`,
`revolve`, `loft`, `shell`, `thicken`, `subtract`, `intersect`, `union`,
`boolean`, `pattern`, `array`, `flip`, `groove`, `notch`, `emboss`,
`engrave`, `stitch`, `combine`, `remove`, `delete`, `widen`, `deepen`,
`raise`, `scale`, `rotate`, `move`, `copy`, `dimension`, `constrain`,
`align`, `position`, `taper`, `tap`, `countersink`, `verify`, `ensure`.
Notably `extrude` (0) never appears where the task brief hypothesized it
as a `pad` synonym, and `position` (0) is always a noun ("the position")
in this corpus, never an imperative.

## 3. Classification

**(a) Real FreeCAD modelling operations** — 27 lemmas, 660 occurrences
(75%), change or create solid/sketch geometry:
`cut, pocket, sketch, pad, fillet, round, add, apply, drill, counterbore,
chamfer, mirror, fuse, join, extend, trim, sweep, machine, mill, thread,
bore, split, narrow, replace, terminate, offset, draw`.
(`apply` and `draw` are placed here because in this corpus they are used
as direct synonyms for `fillet` and `sketch` respectively — see §4.)

**(b) Non-operations** — 20 lemmas, 218 occurrences (25%), that describe
process, reference, naming, or verification rather than commanding a
geometry change. The task's own examples (`open`, `close`, `select`,
`verify`, `ensure`, `note`) are confirmed present as this category, though
`verify`/`ensure` never actually occur (0 matches) — this corpus never
phrases a Step as "verify..."/"ensure...", it phrases refusals instead
("No document change is possible: ..."):
`create, form, assign, use, set, make, centre, place, select, build,
close, label, name, open, note, locate, define, confirm, check, attach`.

`create`/`make`/`build` deserve a flag: they are the most frequent
non-operation verbs (44+13+5 = 62) and are always followed by a FreeCAD
`TypeId`-shaped object name (`Create a PartDesign::Pad named ...`,
`Create a Sketcher::SketchObject named ...`) — they are a generic
constructor wrapper around whatever the real operation is, not an
operation in their own right.

## 4. Synonym clusters (the highest-value output)

| cluster | members (count) | total | reading |
|---|---|---|---|
| material removal | cut (154), pocket (131) | 285 | Used interchangeably for "remove material via a sketch, through all / to depth" — no consistent distinction between Part `Cut` and PartDesign `Pocket` semantics in the prose. |
| edge rounding | fillet (24), round (20), apply\*-fillet (23) | 67 | "Apply R2 fillets to ..." / "round the corner" / "fillet the edge" — three surface forms, one operation. |
| 2D profile creation | sketch (122), draw (10) | 132 | `draw` is a minority synonym for `sketch`. |
| generic instantiation | create (44), make (13), build (5) | 62 | Same "instantiate a FreeCAD object" meta-verb, three spellings. |
| boolean union | fuse (5), join (7) | 12 | Same Boolean-union operation. |
| hole-making | drill (8), bore (1), counterbore (4) | 13 | Overlapping vocabulary for circular material removal that FreeCAD itself splits into distinct `Hole` parameters (simple/counterbore/countersink), collapsed to prose synonyms here. |
| geometry editing | trim (2), split (1) | 3 | Rare, but same "cut a wire/face at a point" family. |
| naming | name (5), label (5) | 10 | Both mean "assign an internal object Label", used inside `Create`/`sketch` clauses, not as standalone operations. |
| positioning/reference | place (11), locate (3), centre (12), set (16) | 42 | All describe *where* a shape sits, not *what* operation runs; `centre` is mostly the participle "... centred at (x, z)" glued onto a `sketch` Step, not a separate command. |

\* `apply` counted under "edge rounding" only for its fillet-sense
occurrences (23/23 sampled were "apply R2 fillet(s) to ..."); it is not a
separate operation.

9 clusters, 626 of 878 occurrences (71%) fall inside one. This is the case
for closure: the 47-lemma "open" surface list reduces to roughly
**9 synonym groups + ~20 stand-alone real operations** once duplicates are
merged — well within reach of a fixed ASD-STE100-style verb list, provided
the non-operations in §3(b) are pulled out of the Step text entirely
(they belong in constraints/selection logic, not in "the operation this
Step commands").

## 5. Multiple operations per Step

Of the 414 English operation-bearing Steps:

| ops detected in one Step | # Steps |
|---|---|
| 1 | 161 |
| 2 | 112 |
| 3 | 97 |
| 4 | 28 |
| 5 | 9 |
| 6 | 4 |
| 7 | 3 |

**253/414 (61%) Steps contain more than one detected operation verb** —
"one Step = one operation" is the minority case in the current Plan
phrasing, not the norm. A single Step commonly chains
sketch→pad→[named feature] or cut→counterbore, or narrates several
positioning/creation clauses ("sketch ... and pad it ... fusing it to the
ring band") as one prose sentence.

## 6. Caveats

- The extractor is a regex heuristic tuned by spot-checking samples, not a
  POS tagger. Precision was checked lemma-by-lemma for every lemma with
  count > 5 (§1); lemmas with residual noun/adjective leakage after
  tuning (`centre`, `name`, `form`) are called out explicitly rather than
  silently trusted — `centre` in particular is dominated by the
  participial "... centred at (x, z)" position clause rather than an
  imperative "centre X" command.
- 167 Ukrainian-language Steps were excluded outright (out of scope for an
  English controlled-language standard) rather than machine-translated,
  since translation would introduce its own vocabulary choices into the
  measurement.
- Counts are **occurrences**, not unique Step-operation pairs — a Step
  that says "cut ... and cut ..." twice contributes 2 to `cut`.

## 7. Script

```python
import glob, json, re
from collections import Counter, defaultdict

files = glob.glob('logs/sessions/*.jsonl')
steps = []
for f in files:
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get('kind') == 'plan':
                s = rec.get('data', {}).get('steps')
                if isinstance(s, list):
                    steps.extend(s)
# len(steps) == 584

satisfies_pattern = re.compile(r"\(satisfies:.*?\)\s*$", re.DOTALL)
def strip_satisfies(s):
    return satisfies_pattern.sub('', s).strip()
cleaned_steps = [strip_satisfies(s) for s in steps]

non_ascii_heavy = [i for i, s in enumerate(cleaned_steps)
                    if len(re.findall(r'[а-яА-ЯіїєґІЇЄҐ]', s)) > 10]
no_change = [i for i, s in enumerate(cleaned_steps)
             if s.strip().startswith('No document change') or 'Cannot' in s[:20]]
excluded = set(non_ascii_heavy) | set(no_change)
# len(non_ascii_heavy) == 167, len(no_change) == 3

VERB_STEMS = [
 'sketch','draw','pad','extrude','pocket','cut','drill','fillet','round','chamfer',
 'mirror','revolve','sweep','loft','thicken','shell','fuse','subtract','intersect',
 'trim','offset','project','assign','apply','set','add','create','build','make',
 'place','position','align','attach','select','use','form','machine','mill','bore',
 'counterbore','countersink','tap','thread','taper','close','open','verify','ensure',
 'note','check','confirm','define','constrain','dimension','extend','scale','rotate',
 'move','copy','replace','narrow','split','groove','notch','emboss','engrave','stitch',
 'join','combine','remove','delete','widen','deepen','raise','center','centre',
 'locate','label','name','pattern','array','flip','union','boolean','terminate',
]  # 'lower' tested and removed: 0/6 sampled uses were verbs (all adjectival)
EXTRA_PATTERNS = {'label': r"labell(?:ed|ing)?"}
FOLLOW_WORDS = r"(?:the|an?|its|it|them|both|all|two|three|four|five|each|those|this|these|his|her|their)"
clause_split_re = re.compile(r"\.\s+|;\s*|:\s+|—|\s+and\s+|\s+then\s+|,\s+then\s+")

verb_occurrences = []
for i, s in enumerate(cleaned_steps):
    if i in excluded:
        continue
    clauses = clause_split_re.split(s)
    clause_first_words = {
        m.group(1).lower()
        for cl in (c.strip().strip(',').strip() for c in clauses)
        for m in [re.match(r"^([A-Za-z']+)(?!-)", cl)] if m
    }
    for stem in VERB_STEMS:
        patt = stem + r"(e?s|e?d|ing)?"
        extra = EXTRA_PATTERNS.get(stem)
        alt = f"(?:{patt}|{extra})" if extra else patt
        for m in re.finditer(r"\b" + alt + r"\b(?!-)", s, re.IGNORECASE):
            word = m.group(0).lower()
            after = s[m.end():m.end() + 30]
            follow_match = re.match(r"\s+" + FOLLOW_WORDS + r"\b", after, re.IGNORECASE)
            follow_proper = re.match(r"\s+([A-Z][A-Za-z:]*)\b", after)
            proper_ok = bool(follow_proper and follow_proper.group(1) not in
                              ('XZ', 'XY', 'YZ', 'X', 'Y', 'Z', 'R'))
            is_ci = word in clause_first_words
            if follow_match or proper_ok or is_ci:
                verb_occurrences.append((stem, i, m.start()))

merged = Counter()
for stem, i, start in verb_occurrences:
    merged['centre' if stem == 'center' else stem] += 1
# sum(merged.values()) == 878, len(merged) == 47

total = sum(merged.values())
sorted_verbs = merged.most_common()
cum, cov90, cov99 = 0, None, None
for idx, (w, c) in enumerate(sorted_verbs):
    cum += c
    if cov90 is None and cum / total >= 0.9:
        cov90 = idx + 1
    if cov99 is None and cum / total >= 0.99:
        cov99 = idx + 1
singletons = sum(1 for w, c in sorted_verbs if c == 1)
# cov90 == 18, cov99 == 41, singletons == 5

per_step = defaultdict(list)
for stem, i, start in verb_occurrences:
    per_step[i].append('centre' if stem == 'center' else stem)
multi_op_steps = sum(1 for v in per_step.values() if len(v) > 1)
# len(per_step) == 414, multi_op_steps == 253
```
