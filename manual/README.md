# manual/

Test material for FreeCadCopilot: drawings and photos of single manufactured mechanical
parts, used to check that the tool can build a working FreeCAD model from a real input.
See `MATERIAL_BRIEF.md` (not in this repo) for how this material was selected and what
counts as good material.

## modelmania/

Phase sheets from the SolidProfessor/Modelmania contests (2023-2025) plus the tests in
`tests/modelmania` that grade against them. Dimensioned engineering drawings, already in
the repo before this material pass.

## drawings/

One directory per source, each with a `manifest.yaml` listing every file: the part, its
licence, a difficulty 1-5, the modelling features it exercises, and whether the sheet
carries dimensions. Drawings are PDFs (or a page rendered to PNG when the PDF was too
large) of a single dimensioned mechanical part -- a bracket, a housing, a machined plate --
picked from public-domain or CC-licensed sources.

`drawings/apollo11-hatch-smithsonian/`: 12 sheets from Project Egress, the Smithsonian's
CC0 teardown of the Apollo 11 hatch -- a modern CAD sheet per part, title block, sections
and real dimensions. `drawings/griffin-machine-drawing-1914/`: 6 parts cropped from one
plate of Griffin's 1914 *Machine Drawing*, public domain, scanned at archive.org.

## photos/

Same manifest shape as `drawings/`, one directory per source (`wikimedia-commons/` and
`thingiverse/`). Each entry is a photograph of a single manufactured mechanical part whose shape is
readable well enough to redraw -- a bearing, a flange, a valve body, a clamp -- not an
assembly, not a whole machine, and not a measuring tool or a pile of fasteners. All files
are CC0, CC-BY, CC-BY-SA or public domain, checked per file on Wikimedia Commons.

`photos/wikimedia-commons/`: 14 photos, difficulty 1-5 -- a deep-groove ball bearing, a
pillow block bearing housing, a blind flange and a flange adaptor, 2 check valves, an
adjustable clamping lever, a plastic valve handwheel, a connecting rod, a spur gear, a
camshaft, a crank handle, and 2 door hinges (a butt hinge and a barrel-and-pin hinge).

Rejected during selection (kept out for a stated reason, not just skipped): a hand-held
loose bearing ball, tabletop displays of several bearings at once, patent and textbook
line drawings mis-filed under a "photo" category, an annotated hinge photo with text
overlaid on the part, a hinge shown disassembled into loose knuckles, a large ball valve
photographed with a worker standing inside it on a factory floor, a gear photo occluded by
a bike frame, a control panel (a whole machine, not one part), a coupled-shaft flange
photographed as a bolted two-part assembly, a rusted butterfly valve and a rusted gate
hinge shot outdoors with the part hard to read against grime and moss, a bar clamp and a
plastic C-clamp with the whole tool (not the cast body alone) in frame, a spring pinch
clamp, an intake boot with its hose clamp, a bicycle handwheel photographed as a full
tabletop layout of loose hardware (sprocket, bushing, screws), a CAD render mis-filed as
a photo, an annotated camshaft photo with callout labels burned into the image, an
out-of-focus foreground shot of a hinge mechanism, and a "piston" file whose actual
content was an unrelated bolted pipe fitting.

`photos/thingiverse/`: 2 photos, difficulty 1 -- a printed 90-degree angle bracket
mounted on 20x20 aluminium extrusion (gusset and both flanges readable), and a knurled
guitar control knob. Both CC BY or CC BY-SA, licence read off the Thingiverse item page.
Each photo is a "Make" (a community member's photo of their own print of the design), not
the designer's render, so attribution in `manifest.yaml` names the designer for the design
licence and notes the photographer is uncredited on the item page.

Rejected from Thingiverse during this pass: CAD renders and screenshots used as a thing's
main image, multiple identical prints laid out together (a print-bed shot of 3-4 brackets,
a lineup of 5 pulleys, a tray of spools), a knob held in a hand, a caliper with an
unrelated small print attached to it, an RC car photo mis-tagged under a shaft-coupling
listing, any item whose licence was NC or ND or unstated on the page, and three make-photos
that looked fine at thumbnail size but failed on inspection -- a bracket in shadow under a
desk with unreadable edges, a coupling reduced to a small red blob inside a printer frame,
and a pulley occupying under a tenth of the frame with its teeth not resolvable. Sprockets,
printed spur gears, mounting plates and spool holders did not turn up a single-part
in-focus photo under an acceptable licence, and were not chased further once 16 photos
across both sources was reached.

## recorded/

Captured API responses, model lists and session transcripts used by the test suite as
fixtures -- not test material for the tool itself.
