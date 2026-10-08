"""
The model's part of Assertion: one expression per acceptance entry, each evaluated alone in the check namespace.
An expression gives True or False; one that raises, or gives anything else, is a defect in that check and leaves every other entry's result as it was.
"""

def each_expression_evaluated(expressions):
    """The check script that evaluates `{entry: expression}` one at a time, leaving `held`, `failed` and `defects` in its namespace."""
    return "held, failed, defects = check_each(%r)\n" % (dict(sorted(expressions.items())),)


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
