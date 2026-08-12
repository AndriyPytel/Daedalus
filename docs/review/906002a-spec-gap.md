# Where 906002a diverges from FCC-111, 113, 114 and 115

Written after the Spec axis of the review of `906002a` reported four tickets as
implemented-but-not-as-asked. One row per ticket: what the ticket asks in its own
words, what the commit does, what the gap is, and a command that shows it.

Fixed point: `git diff a719f4d..906002a`. Everything below is checkable from the
repo root with the project's own interpreter.

---

## The one cause under three of the four

Three of these four wanted a check where the geometry is — in `apply_step` and
`core/preflight_worker.py`, the only places that hold a built sketch and a built
solid. Nothing carries **what this Step must satisfy** down there: the tool's
arguments are written by the provider (`code`, `label`), and `Applier.apply` →
`PreflightWorker.check` passes `code`, `document`, `render_path` and nothing
else. So each check was put where the data already flows — at plan time, or in
the assertion phase — and each lost something in the move.

The other missing seam is the direction map: the description names its
directions in words ("the length", "the depth front to rear") and a Document has
X, Y and Z, and nothing maps one onto the other. `round_in` (FCC-109) is the
first half of that map and it is not yet joined to the second.

Both are worth their own issue before these four are attempted again.

---

## FCC-111 — one Step, one plane

**The ticket asks**

> The comparison is mechanical: the sketch placement of a Step, against the
> `plane` of every entry that Step cites, and the pocket direction against the
> `axis`. Where they disagree, the Step is wrong before it runs.

**What shipped** — `core/loop.py:1897` `_two_directions_in_one_step`

Cited entries are compared **with each other**: a Step citing two different
values of `plane` (or of `axis`) is sent back to Plan. The sketch placement is
never read, because at plan time no sketch exists.

**The gap** — a Step citing one plane entry is self-consistent whatever its code
does. The recorded Step 1 cited `right-mounting-lug → plane: the plane of the
length and the depth` and wrote
`App.Rotation(App.Vector(1, 0, 0), 90)` — the length-height plane. One entry,
so nothing fires.

```
.venv/bin/python -c "
import sys; sys.path.insert(0,'.')
from core.loop import _two_directions_in_one_step, Plan
one = Plan(steps=['the recorded Step 1'],
           acceptance=['features → right-mounting-lug → plane: the plane of the length and the depth (read)'],
           cites=[['features → right-mounting-lug → plane']])
print('one entry cited ->', _two_directions_in_one_step(one) or 'nothing found')"
```

    one entry cited -> nothing found

What it *does* catch is the other half of the same Step, which cited the outer
profile in the elevation and the lug in the plan together. That is a real catch
and it is not what the ticket asked for.

---

## FCC-113 — one picture, one projection

**The ticket asks**

> The rule that replaces it: crop to the view, at the native 186 DPI of the
> raster the view was placed as, and stop. Tile only a view that genuinely
> exceeds the token budget at its own resolution — which, on every sheet
> measured, is none of them.

**What shipped** — `core/sheet_reader.py:373-390`

`parts = _tiled(rect, dpi, max_edge)` is untouched. When a view comes back as
more than one part, the view is **also** rendered whole, once, at
`_affordable(rect)` — the token budget — and shown before its pieces.

**The gap** — the sheet still goes out as 23 tiled crops; the wholes are five
more pictures beside them, and none of them is at 186 DPI.

```
/Applications/FreeCAD.app/Contents/Resources/bin/python -c "
import sys
sys.path.insert(0, '/Users/apytel/Library/Application Support/FreeCAD/v1-1/AdditionalPythonPackages')
sys.path.insert(0, '.')
from core import sheet_reader as sr
s = sr.read('tests/modelmania/model-mania_-in-person-2023 copy.pdf')
print('crops', len(s.crops), 'of which tiled',
      len([c for c in s.crops if ', part ' in c.name]), '| wholes', len(s.wholes))
print([ (w.name, w.dpi) for w in s.wholes ])"
```

    crops 23 of which tiled 16 | wholes 5
    [('the middle centre of the sheet, on page 1', 224), ...]

**Why it was done this way** — the page DPI is what makes a callout legible
(`LEGIBLE_PX = 50`, measured: 14 px of ink in a digit is what misreads), and
186 DPI is the native resolution of the *view body* only. Dropping the render to
186 to fit a view in one crop takes the resolution off the dimension text, which
is the defect `sheet_reader` exists to remove. That is a real tension in the
ticket, and the honest thing was to say so on the ticket rather than to ship a
different rule quietly. The undone part is genuine: render the body at its own
DPI without taking the callouts down with it.

---

## FCC-114 — a dimension, not just a topology

**The ticket asks**

> Preflight should measure what the Step claimed: the envelope against the
> part's box, and the entries the Step cites against the feature it created — a
> size, a position between two faces, the direction of a pocket.
> ... a Step that failed a check and passed on retry has to be re-measured
> against what it satisfies.

**What shipped** — `core/loop.py:1219` `the_envelope_measured`, run at the top of
`assertion()`; `core/preflight_worker.py` is unchanged; `entries_a_retry_touched`
is added to the assertion request.

**Three gaps**

1. It is not in Preflight, so it does not run per Step. It runs once, at the end.
2. Only the envelope is measured. Neither defect the ticket names is an envelope
   entry — the lug built 25 mm tall where the drawing says 30, the split
   stopping at x=91.5 where the drawing runs it to 108 — so both still pass.
3. The retry half is a line in the assertion request, read by a provider. It is
   a hint, not a measurement.

**And a fourth, found while writing this** — the check assumes the three
envelope entries are the part's three overall extents. On this very sheet they
are not: the description's third entry is *height between the bottom and top
faces of the right lug: 30 mm*, which is a feature's height, while the part is
50 mm tall because the ring is Ø50. So the check fails a **correctly built**
clamp.

```
.venv/bin/python -c "
import sys, re; sys.path.insert(0,'.')
from core.loop import the_envelope_measured
acc = ['envelope → length ...: 108 mm (read)',
       'envelope → depth front to rear ...: 30 mm (read)',
       'envelope → height between the bottom and top faces of the right lug: 30 mm (read)']
asked = eval(re.search(r'asked = (\[.*\])', the_envelope_measured(acc)).group(1))
right = sorted([108.0, 30.0, 50.0])   # a correctly built clamp: the ring is Ø50
print('asked ', asked); print('right ', right)
print('verdict:', 'FAILS a right part' if any(abs(a-b) > 0.5 for a, b in zip(right, asked)) else 'passes')"
```

    asked  [30.0, 30.0, 108.0]
    right  [30.0, 50.0, 108.0]
    verdict: FAILS a right part

The rule the check needs is the one the repo already states for an envelope
entry — it is measured *between two named datums* — and the lug entry names two
faces of a feature instead. Until that is enforced, the envelope check should be
treated as advisory.

---

## FCC-115 — an address resolves, or it is an error

**The ticket asks**

> Where the address resolves, Execute uses the number. Where it does not resolve
> in the plane the Step is working in, that is a defect in the Step, and the
> honest outcome is an error naming the address — not a mean of four
> y-coordinates.

**What shipped** — `core/loop.py:877` sends `what_each_entry_says`
(`_addressed(acceptance)`); `config/prompts/execute.md` says an unresolvable
value is `unbuildable`.

**The gap** — the data half is done and the enforcement half is prose. Nothing
reads the code Execute returns, and nothing checks that a value in it came from
an address. `arm_z = sum(...) / len(arm_lines)` would be applied today exactly
as it was applied then.

```
grep -rn --include=*.py "what_each_entry_says" core/
```

    core/loop.py:877:        request["what_each_entry_says"] = _addressed(entries)

One hit, and it is the write. Nothing reads it back.

---

## Summary

| ticket | data half | enforcement half | catches its own recorded defect |
|---|---|---|---|
| FCC-111 | plan-time citations | yes, but of the wrong thing | only where the Step cites two planes |
| FCC-113 | whole view rendered | n/a | partly — the whole view exists now |
| FCC-114 | envelope sizes | end of run, advisory | no, and it can fail a right part |
| FCC-115 | addressed entries sent | prompt only | no |

FCC-108, FCC-109, FCC-110 and FCC-112 were reported by the same axis as
implemented as asked.
