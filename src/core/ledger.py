"""A run-scoped, distilled memory of classified Preflight/lint/apply rejections.

Survives the whole run: one is created per call to `run()` and carried across
every later Step, every retry, and every replan. Entries are deduplicated by
classification, not by Step. `max_lines` = `len(phrase_table) + 1`: the count
of distinct lines this ledger can ever hold.
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
