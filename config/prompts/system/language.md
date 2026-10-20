The vocabulary every phase writes and reads. A `kind`, an `op`, a datum name
and an envelope key are each one of a closed list, and this file is the one
place any of them is spelled out — `core/loop.py::prompt` composes each
phase's own prompt from the sections below it actually uses, so a phase's
own prompt never redefines a term this file defines.

## Naming from a closed vocabulary

Every reply carries `"kind_vocabulary": "closed"` at the top level. A `kind`
off the list below is refused rather than merged, and the entry that refuses
it names what it maps onto instead — a value worth re-sending correctly, not
a rejection to argue with.

Feature `id`: `<kind>-<place>` where kind is one of (hole, slot, pocket,
cutout, pad, fillet, chamfer, boss, rib, thread, counterbore, countersink,
groove, notch, tab, lug, ear, flange, step, shoulder, shell, draft) and place
is one or two of (left, right, front, rear, top, bottom, upper, lower, centre,
inner, outer); `front-inner` never `inner-front`. Write the operation, not a
synonym for it: a bore is a `hole`, a round is a `fillet`, an opening or a
window is a `cutout`, a split or a saw cut is a `slot`, a recess is a
`counterbore`. Write the shape, not the drawing's word for the region it sits
in: an arm, a ring, a web, a band, a collar, a profile, a plate, a body, an
outline or a contour is a `pad`. Put that word in the new `region` field
instead — `region` is open text off the drawing and is never a value in
`kind`, in `id`'s place slot, or in any other closed list here. A dimension
the drawing gives — a diameter, a radius, a length, an angle — is never in
`kind` either; it goes in its own field (`diameter`, `outer_diameter`,
`radius`, `thickness`, `length`, `width`, `depth`, `angle`, `count`, `pitch`,
`bolt_circle_diameter`). Append `-4x` for a repeated feature.

`direction` marks the measured-from end (empty if unknown); `goes_through`,
never inferred from the word "through" in `topology`. A rib's sloped top
carries `slope` and `rounding`; never re-derived from `height`/`position`
by hand.

Datum `name`: one of (the left end face, the right end face, the front face,
the rear face, the top face, the bottom face, the ring axis, the bore axis,
the centre plane, the longitudinal centre plane, the transverse centre plane).

Envelope key: one of (length, width, height, depth front to rear, depth left
to right, thickness).

## The operation is one of 36

Every reply carries `"op_vocabulary": "closed"` at the top level, and every
Step carries an `op` naming what it does, one of (pad, revolve,
additive-loft, additive-sweep, additive-helix, additive-primitive, pocket,
hole, groove, subtractive-loft, subtractive-sweep, subtractive-helix,
subtractive-primitive, fillet, chamfer, draft, thickness, mirror,
linear-pattern, polar-pattern, multi-transform, scale, sketch, datum-plane,
datum-line, datum-point, local-coordinate-system, sub-shape-binder, body,
fuse, cut, common, thread, extend, trim, offset). No `other`: a Step needing
an operation off this list is refused rather than built out of whichever one
is nearest, and comes back to you naming the verb it could not place.

A thread is a `hole` whose prose gives the thread's own parameters — its
pitch, its depth, its designation such as M8 x 1.25 — the way any other
`hole`'s prose gives a diameter and a depth; a helix cut into an existing
hole's wall is never a Step of its own. A step or a shoulder in the part is
a `pad` where the material stands proud of the neighbouring face and a
`pocket` where it is cut back from it — which one depends on which side of
the step the material is on, never on the word "step" itself, which this
Plan already uses for something else.

A drawing that shows a flat pattern and formed views describes the formed part;
the flat pattern only gives developed lengths. Build the formed part.

A bent sheet part is built from its walls and its bends. Each wall is a `pad`
of the sheet thickness in its own plane, placed where the fold puts it, not in
the flat pattern's plane. Each bend between two walls is a `revolve` of the
sheet-thickness section (inner radius R, outer R + thickness) by the bend
angle about the bend axis. Flat-pattern lengths are developed lengths: a
wall's own length is its flat length minus its share of the bend allowance.

A thin-walled or cored body is shelled or pocketed: pad the outside, then take
the inside out with a `thickness` or a `pocket`. Never pad the envelope solid
and leave the cavity out.

## A Step is one sentence

A Step names one operation. It is phrased as an instruction, not the outcome
the operation produces, and every number it needs is written into its own
prose rather than left for the code to invent. The operation is named with
its own word from the closed list above, never a synonym for it.

## The artefacts are English

The description, the Plan and every closed value they carry are written in
English. Ukrainian — or any other language the drawing or the conversation
uses — stays the language of the conversation and of the Intent; it is
refused in a `kind`, an `op`, a datum name, an envelope key, or the prose of
a Step.
