"""
`config/prompts/design.md`'s closed `kind` vocabulary and its region-name
clause.

The vocabulary is read out of `config/prompts/design.md` itself, never
copied into this file: a test carrying its own copy of the closed list
passes even while the prompt says something else, which is the defect this
whole epic is about. Coverage is measured against
`tests/recorded/vocabulary_kinds.json` (47 design-model sessions, 410
features), and 6 residual features remain, each of them named.
"""
import json
import os
import re

from core.prompts import ROOT, read_prompt
from tests import modelmania_harness

VOCABULARY_KINDS = modelmania_harness.RECORDED_DIR / "vocabulary_kinds.json"


def _vocabulary_md():
    return read_prompt("language.md")


def _naming_section(text):
    """
    The closed `kind`/`id`/datum/envelope vocabulary. It lives in
    `language.md` now, composed into `design.md` by
    `core.loop.prompt` rather than written there a second time.
    """
    start = text.index("## Naming from a closed vocabulary")
    end = text.index("## The operation is one of 36")
    return text[start:end]


def _kind_vocabulary(section):
    """The closed `kind` list plus the banned-prose-synonym sentences that
    follow it, both read out of the prompt's own wording rather than
    restated here."""
    listed = re.search(r"kind is one of \(([^)]+)\)", section)
    kinds = [w.strip() for w in listed.group(1).split(",")]

    synonyms = {}
    flat = section.replace("\n", " ")
    for sentence in re.split(r"(?<=[.!?])\s+", flat):
        rest = sentence.rsplit(":", 1)[-1]
        pos = 0
        for kind_match in re.finditer(r"is an? `([a-zA-Z]+)`", rest):
            phrase = rest[pos:kind_match.start()].strip().strip(",").strip()
            kind = kind_match.group(1)
            for part in re.split(r"\bor\b|,", phrase):
                part = re.sub(r"^(a|an)\s+", "", part.strip())
                part = re.sub(r"\s+", " ", part)
                if part:
                    synonyms.setdefault(kind, []).append(part)
            pos = kind_match.end()
    return kinds, synonyms


def _bare_word_lists(text):
    """Every parenthesised, comma-separated list in the prompt whose entries
    are all single bare words — the shape a closed vocabulary is written in
    here. A datum name or an envelope key is a multi-word phrase and is
    never one of these."""
    lists = []
    for paren in re.finditer(r"\(([^()]+)\)", text):
        parts = [p.strip() for p in paren.group(1).split(",")]
        if len(parts) >= 2 and all(re.fullmatch(r"[a-z][a-z-]*", p) for p in parts):
            lists.append(parts)
    return lists


def _stem(word):
    word = word.lower()
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if re.search(r"(s|x|z|ch|sh)es$", word) and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and len(word) > 3 and not word.endswith("ss"):
        return word[:-1]
    return word


def _classify(text, kinds, synonyms):
    """The closed kind (or a banned prose synonym of one) found in `text`,
    matching on a whole word or a whole multi-word phrase, plural forms
    folded onto the singular. `None` when nothing in the vocabulary is
    present — the feature is residue."""
    lowered = text.lower()
    candidates = list(kinds)
    for kind, words in synonyms.items():
        candidates.extend(words)
    for word in sorted(candidates, key=len, reverse=True):
        if " " in word:
            if word in lowered:
                return word
        else:
            tokens = re.findall(r"[a-zA-Z]+", lowered)
            if any(_stem(token) == _stem(word) for token in tokens):
                return word
    return None


def test_the_kind_vocabulary_covers_all_but_six_of_410_features():
    """Residue target from this ticket's Acceptance Criteria: 6 or fewer of 410,
    each one named: two `material specification` entries, one reference
    region, and three snap-latch features no kind names cleanly."""
    kinds, synonyms = _kind_vocabulary(_naming_section(_vocabulary_md()))

    with open(VOCABULARY_KINDS, encoding="utf-8") as f:
        corpus = json.load(f)

    residue = []
    for model in corpus["models"]:
        for feature in model["features"]:
            text = feature["id"] + " " + feature["kind"]
            if _classify(text, kinds, synonyms) is None:
                residue.append((model["session"], feature["id"], feature["kind"]))

    assert len(residue) <= 6, residue


def test_no_word_appears_in_two_of_designs_bare_vocabularies():
    """`ear` is the fixture: before this change it named a closed `kind` and,
    two lines later, a region-name example — the same word a term and not a
    term at once."""
    lists = _bare_word_lists(_naming_section(_vocabulary_md()))

    seen = {}
    duplicates = {}
    for entries in lists:
        for word in set(entries):
            seen[word] = seen.get(word, 0) + 1
            if seen[word] > 1:
                duplicates[word] = seen[word]

    assert not duplicates, duplicates


def test_no_prompt_licenses_a_value_outside_a_closed_list():
    """The deleted region-name clause — `"Use the drawing's region name
    (arm, ear, web) when it names one"` — is the fixture: it let any word a
    draughtsman wrote become a legal `kind`/`place` value."""
    offenders = []
    for dirpath, _dirs, names in sorted(os.walk(ROOT)):
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                text = f.read()
            if "the drawing's region name" in text:
                offenders.append(name)

    assert not offenders, offenders


def test_core_design_feature_kind_matches_the_prompts_own_list():
    """
    `core.design.FEATURE_KIND` is the closed list `merge()` refuses a `kind`
    against; this prompt is what asks the model for it.
    Two independent copies of a closed list is exactly the shape that drifts
    silently, so a test keeps them equal rather than a comment promising it.
    """
    from core.design.core import FEATURE_KIND

    kinds, _ = _kind_vocabulary(_naming_section(_vocabulary_md()))

    assert tuple(kinds) == FEATURE_KIND
