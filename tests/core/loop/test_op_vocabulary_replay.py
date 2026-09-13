"""
Replays `tests/recorded/vocabulary_steps.json`'s English Steps through the
same operation-verb extraction measured over `logs/` (which is gitignored,
hence the tracked fixture, already established as the substitute), then
collapses every detected verb onto the closed 36-`op` vocabulary the way
its synonym clusters and its real-operation classification say to: `round`/`apply` fold onto `fillet`, `drill`/`bore`/`counterbore`
onto `hole`, `join`/`union` onto `fuse`, `draw` onto `sketch`, and so on. A
constructor verb (`create`/`make`/`build`) followed by an object noun or a
literal TypeId — `"Create a PartDesign::Pad named ..."` — is resolved off
that noun instead, via `core.lint.OP_TYPEIDS`'s own reverse mapping, since
the decision that closed the vocabulary calls this out by name:
"`create`/`make`/`build` ... are a generic constructor wrapper around
whatever the real operation is."

The extraction regex, `VERB_STEMS`, the English-language filter (167
non-ASCII-heavy Steps and 3 refusals excluded) and the `(satisfies: …)`
strip are copied from `ste-plan-verbs.md` §7's script rather than
reimplemented, so the two stay one method read twice.

The synonym and noun tables are test-only glue between an open corpus and
the closed list — never a second copy of the list itself, which is why
every assigned value is checked against `core.loop.OP_VERBS`.
"""
import json
import re
from collections import defaultdict

from core.lint import OP_TYPEIDS
from core.loop.plan import OP_VERBS
from tests import modelmania_harness

VOCABULARY_STEPS = modelmania_harness.RECORDED_DIR / "vocabulary_steps.json"

VERB_STEMS = [
    "sketch", "draw", "pad", "extrude", "pocket", "cut", "drill", "fillet", "round",
    "chamfer", "mirror", "revolve", "sweep", "loft", "thicken", "shell", "fuse",
    "subtract", "intersect", "trim", "offset", "project", "assign", "apply", "set",
    "add", "create", "build", "make", "place", "position", "align", "attach", "select",
    "use", "form", "machine", "mill", "bore", "counterbore", "countersink", "tap",
    "thread", "taper", "close", "open", "verify", "ensure", "note", "check", "confirm",
    "define", "constrain", "dimension", "extend", "scale", "rotate", "move", "copy",
    "replace", "narrow", "split", "groove", "notch", "emboss", "engrave", "stitch",
    "join", "combine", "remove", "delete", "widen", "deepen", "raise", "center",
    "centre", "locate", "label", "name", "pattern", "array", "flip", "union",
    "boolean", "terminate",
]
EXTRA_PATTERNS = {"label": r"labell(?:ed|ing)?"}
FOLLOW_WORDS = (r"(?:the|an?|its|it|them|both|all|two|three|four|five|each|those|"
               r"this|these|his|her|their)")
CLAUSE_SPLIT = re.compile(r"\.\s+|;\s*|:\s+|—|\s+and\s+|\s+then\s+|,\s+then\s+")
SATISFIES = re.compile(r"\(satisfies:.*?\)\s*$", re.DOTALL)
CYRILLIC = re.compile(r"[а-яА-ЯіїєґІЇЄҐ]")

OP_SYNONYMS = {
    "sketch": "sketch", "draw": "sketch",
    "pad": "pad", "extrude": "pad", "add": "pad",
    "pocket": "pocket",
    "cut": "cut", "subtract": "cut", "machine": "cut", "mill": "cut",
    "drill": "hole", "bore": "hole", "counterbore": "hole", "countersink": "hole",
    "fillet": "fillet", "round": "fillet", "apply": "fillet",
    "chamfer": "chamfer",
    "mirror": "mirror", "flip": "mirror",
    "revolve": "revolve",
    "sweep": "additive-sweep",
    "loft": "additive-loft",
    "shell": "thickness", "thicken": "thickness",
    "fuse": "fuse", "join": "fuse", "union": "fuse",
    "intersect": "common", "combine": "common",
    "trim": "trim", "split": "trim", "terminate": "trim",
    "offset": "offset",
    "tap": "thread", "thread": "thread",
    "taper": "draft",
    "extend": "extend",
    "scale": "scale",
    "groove": "groove",
    "pattern": "linear-pattern", "array": "linear-pattern",
}

NOUN_TO_OP = {
    "sketch": "sketch", "pad": "pad", "pocket": "pocket", "chamfer": "chamfer",
    "fillet": "fillet", "body": "body", "hole": "hole", "cut": "cut",
    "groove": "groove", "thread": "thread", "mirror": "mirror",
    "revolution": "revolve", "draft": "draft", "thickness": "thickness",
    "common": "common", "offset": "offset",
}

TYPEID_TO_OP = {}
for _op, _tids in OP_TYPEIDS.items():
    for _tid in _tids:
        TYPEID_TO_OP.setdefault(_tid, _op)


def _steps():
    with open(VOCABULARY_STEPS, encoding="utf-8") as f:
        corpus = json.load(f)
    return [step for plan in corpus["plans"] for step in plan["steps"]]


def _english(steps):
    cleaned = [SATISFIES.sub("", s).strip() for s in steps]
    excluded = {i for i, s in enumerate(cleaned)
                if len(CYRILLIC.findall(s)) > 10
                or s.strip().startswith("No document change") or "Cannot" in s[:20]}
    return [s for i, s in enumerate(cleaned) if i not in excluded]


def _verbs_in(step):
    clauses = CLAUSE_SPLIT.split(step)
    clause_first_words = {
        m.group(1).lower()
        for clause in (c.strip().strip(",").strip() for c in clauses)
        for m in [re.match(r"^([A-Za-z']+)(?!-)", clause)] if m
    }
    found = []
    for stem in VERB_STEMS:
        pattern = stem + r"(e?s|e?d|ing)?"
        extra = EXTRA_PATTERNS.get(stem)
        alt = "(?:%s|%s)" % (pattern, extra) if extra else pattern
        for m in re.finditer(r"\b" + alt + r"\b(?!-)", step, re.IGNORECASE):
            word = m.group(0).lower()
            after = step[m.end():m.end() + 30]
            follow_word = re.match(r"\s+" + FOLLOW_WORDS + r"\b", after, re.IGNORECASE)
            follow_proper = re.match(r"\s+([A-Z][A-Za-z:]*)\b", after)
            proper_ok = bool(follow_proper and
                             follow_proper.group(1) not in ("XZ", "XY", "YZ", "X", "Y", "Z", "R"))
            if follow_word or proper_ok or word in clause_first_words:
                found.append("centre" if stem == "center" else stem)
    return found


def _secondary_op(step):
    for tid in sorted(TYPEID_TO_OP, key=len, reverse=True):
        if tid in step:
            return TYPEID_TO_OP[tid]
    lowered = step.lower()
    for noun, op in NOUN_TO_OP.items():
        if re.search(r"\b" + noun + r"\b", lowered):
            return op
    return None


def _classify(step):
    """
    The `op` this Step resolves to, plus every distinct closed op detected —
    `op` is the first, so a compound legacy Step ("sketch ... and pad it")
    still gets exactly one, the way a revised Plan shaped by the closed
    vocabulary would
    have split it into two Steps instead. `None` is the residue: no real
    operation verb, and no constructor noun or TypeId either — a Step
    describing something outside geometry entirely, most often a material
    card.
    """
    canon = []
    for verb in _verbs_in(step):
        mapped = OP_SYNONYMS.get(verb)
        if mapped and mapped not in canon:
            canon.append(mapped)
    if not canon:
        secondary = _secondary_op(step)
        if secondary:
            canon = [secondary]
    return (canon[0] if canon else None), canon


def test_the_english_steps_are_397_matching_the_tracked_fixture():
    """
    584 raw Steps minus 167 non-ASCII-heavy minus 3 refusals is 414 —
    `ste-plan-verbs.md`'s own count, measured over `logs/`. The tracked
    fixture is a 567-Step subset of that same corpus (already established
    as the substitute for a gitignored directory), and the same filter
    over it yields 397, not 414: a fixture is a sample of the sessions that
    happened to be captured, not the full corpus re-measured.
    """
    assert len(_steps()) == 567
    assert len(_english(_steps())) == 397


def test_almost_every_english_step_resolves_to_one_of_the_36_ops():
    """
    9 of 397 resolve to nothing: 7 assign a material card, 1 sets the
    document's units, 1 rewrites a Step to satisfy a later revision — none
    of them a geometry operation, and none of the 36 was ever going to
    name one. This is the fixture's residue, the same shape
    `test_vocabulary_closure.py` reports for `kind` residue over
    `vocabulary_kinds.json`, and it is what the decision that closed the
    vocabulary means where it says
    the non-operations "have nowhere to go" once `op` is a closed field.
    """
    residue = [step for step in _english(_steps()) if _classify(step)[0] is None]

    assert len(residue) <= 9, residue


def test_every_resolved_op_is_one_of_the_36():
    for step in _english(_steps()):
        op, _ = _classify(step)
        if op is not None:
            assert op in OP_VERBS, (op, step)


def test_159_english_steps_resolve_to_more_than_one_closed_op():
    """
    Collapsed onto the closed list, 159 of 397 still name more than one
    operation in a single Step — well down from the 253 of 414 measured
    over the open 47-lemma surface vocabulary (many raw multi-verb Steps,
    such as "fillet ... and round ...", collapse onto the same closed `op`
    and are single once merged), but still the majority case for a Step
    that predates the "one Step, one op" rule.
    """
    multi = [step for step in _english(_steps()) if len(_classify(step)[1]) > 1]

    assert len(multi) == 159, len(multi)
