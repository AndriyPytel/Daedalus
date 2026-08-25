"""
A run-scoped, distilled memory of classified Preflight/lint/apply rejections.

`attempts` (`core/loop.py`) is per-Step and is thrown away the moment a Step
lands or is skipped, so a rake hit at Step 4 is invisible to Step 5 and to a
replan — the run repeats a mistake it already paid for once. A `Ledger`
survives both: one is created per call to `run()` and is never reset, only
appended to, so it is carried across every later Step, every retry of a Step,
and every replan of the same run.

It is deliberately not a transcript. `record()` never takes code and never
keeps a traceback past the call that classifies it. An entry is identified by
its *classification* alone — the same phrase from `_FAILED_THIS_WAY`
(`core/loop.py`) that already drives "you have failed this the same way
twice" — not by which Step hit it: a run that moves no material at Step 3 and
again at Step 7 has failed one way, not two, and the second occurrence
increments the first entry's count rather than adding a line for a different
Step. What each line keeps of "what was asked for" is the prose of the Step
that hit it *first*, trimmed to `_STEP_CHARS` — an example, not a log.

Deduplicating by classification rather than by (Step, classification) is what
makes `max_lines` a fact about the vocabulary rather than a number invented
for this ledger: `classify()` can return only one of `len(phrase_table)`
labels, or `_UNCLASSIFIED_LABEL` for everything else, so `len(phrase_table) +
1` is not a cap chosen to sound reasonable — it is the count of distinct
lines this ledger can ever hold, however many Steps a run has. Growing
`_FAILED_THIS_WAY` grows `max_lines` with it, because `max_lines` is computed
from the table handed to the constructor, not written down separately.

`max_chars` is the same kind of fact rather than a guess: the exact number of
characters `max_lines` lines render as when each one pairs the longest label
the table has with a Step long enough to hit `_STEP_CHARS`, computed once at
construction (`tests/test_loop.py` asserts it against the real
`_FAILED_THIS_WAY`). That count alone can never grow past `max_chars` — every
line is already at its longest — so the one thing that can push a render past
it is the "(x123)" a repeated entry grows once every line is already
occupied by a distinct classification, and that is what `_enforce_caps` is
actually defending against: a run long enough to fail every known way once
and then fail its favourite way another few hundred times.

The line count itself has nowhere to grow past `max_lines`, because
`classify()` cannot produce a label the table does not have — `_enforce_caps`
still trims by line count first, and drops the oldest entry, purely so a
future bug that classified something outside the table would fail loud
(a line that should not exist, immediately capped and visible) rather than
growing the prompt without bound.

Both caps drop the *oldest distinct entry* first, in the order it was first
recorded — never the most recent, because the most recent failure is the one
the next attempt is written against.

A traceback that matches nothing in the phrase table is not dropped: it is
recorded under `_UNCLASSIFIED_LABEL`, one shared line for every failure this
build does not know how to name. Losing it silently would tell a run that
repeats an unrecognised mistake five times that it has never happened before,
which is worse than the honest "this failed and this build does not know
what kind of failure it was."
"""
from collections import OrderedDict

_STEP_CHARS = 60

_UNCLASSIFIED_LABEL = "failed for a reason this build's known patterns do not cover"


def _short(step):
    step = str(step or "").strip()
    return step if len(step) <= _STEP_CHARS else step[:_STEP_CHARS - 1].rstrip() + "…"


def _classify(traceback, phrase_table):
    text = traceback or ""
    for phrase, _, label in phrase_table:
        if phrase in text:
            return label
    return _UNCLASSIFIED_LABEL


def _line(step, label, count=1):
    suffix = "" if count == 1 else " (x%d)" % count
    return "- %s: %s%s" % (step, label, suffix)


def _worst_case_chars(phrase_table, max_lines):
    labels = sorted((label for _, _, label in phrase_table), key=len, reverse=True)
    labels = (labels + [_UNCLASSIFIED_LABEL])[:max_lines]
    worst_step = "x" * _STEP_CHARS
    return sum(len(_line(worst_step, label)) for label in labels) \
        + max(0, len(labels) - 1)


class Ledger:
    """
    What this run has already tried and how it failed, capped and deduplicated.

    `phrase_table` is `_FAILED_THIS_WAY` (`core/loop.py`) — the same phrases
    Execute's own "failed this the same way twice" directive is keyed on, so a
    failure this build stops recognising stops being classified here too
    rather than being classified wrongly.
    """

    def __init__(self, phrase_table=()):
        self._phrase_table = tuple(phrase_table)
        self._entries = OrderedDict()
        self.max_lines = len(self._phrase_table) + 1
        self.max_chars = _worst_case_chars(self._phrase_table, self.max_lines)

    def record(self, step, traceback):
        label = _classify(traceback, self._phrase_table)
        if label in self._entries:
            self._entries[label][1] += 1
        else:
            self._entries[label] = [_short(step), 1]
        self._enforce_caps()

    def render(self):
        return "\n".join(_line(step, label, count)
                          for label, (step, count) in self._entries.items())

    def __bool__(self):
        return bool(self._entries)

    def _enforce_caps(self):
        while len(self._entries) > self.max_lines:
            self._entries.popitem(last=False)
        while len(self._entries) > 1 and len(self.render()) > self.max_chars:
            self._entries.popitem(last=False)
