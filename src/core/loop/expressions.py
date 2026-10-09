"""
The model's part of Assertion: one expression per acceptance entry, each evaluated alone in the check namespace.
An expression gives True or False; one that raises, or gives anything else, is a defect in that check and leaves every other entry's result as it was.
"""

import json

TOO_SLOW = "the expression took longer than the kernel allows"


def each_expression_evaluated(expressions, progress=None):
    """The check script that evaluates `{entry: expression}` one at a time, leaving `held`, `failed` and `defects` in its namespace; *progress* names the file the kernel writes each entry to as it goes."""
    head = "check_progress = %r\n" % (progress,) if progress else ""
    return head + "held, failed, defects = check_each(%r)\n" % (dict(sorted(expressions.items())),)


def progress_read(path):
    """What a killed `each_expression_evaluated` call left in *path*: `(held, failed, defects, in_flight)`, the last being the entry that started and never finished, or None."""
    held, failed, defects, started = [], [], [], None
    try:
        lines = open(path).read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            note = json.loads(line)
        except ValueError:
            continue
        entry = note["entry"]
        if note.get("started"):
            started = entry
            continue
        started = None
        if "defect" in note:
            defects.append([entry, note["defect"]])
        else:
            (held if note["verdict"] else failed).append(entry)
    return held, failed, defects, started


def answers_in(declared, entries):
    """
    What a declaration answered for the entry numbers in *entries*: `(expressions, uncovered, unanswered)`.
    An item that is not an object, names an entry not asked, repeats one already answered, or gives neither a string `expr` nor a string `uncovered` leaves its entry unanswered.
    """
    items = declared.get("checks") if isinstance(declared, dict) else None
    expressions, uncovered = {}, {}
    for item in items if isinstance(items, list) else ():
        entry = item.get("entry") if isinstance(item, dict) else None
        if (not isinstance(entry, int) or isinstance(entry, bool) or entry not in entries
                or entry in expressions or entry in uncovered):
            continue
        expression, reason = item.get("expr"), item.get("uncovered")
        if isinstance(expression, str) and expression.strip():
            expressions[entry] = expression.strip()
        elif isinstance(reason, str) and reason.strip():
            uncovered[entry] = reason.strip()
    return expressions, uncovered, [entry for entry in entries if entry not in expressions and entry not in uncovered]
