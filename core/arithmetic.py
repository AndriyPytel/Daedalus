"""
The sums a Step states about itself, worked out.

A Step is prose, and prose can assert arithmetic nobody does. In the recorded
run (`logs/sessions/4fca495a8076439a9967a584abd05227.jsonl`) the description
gave a 45° arm 73 mm long from the ring axis and an overall length that puts
the left tangent plane 65.5 mm from that axis, which cannot both be true —
73 * cos(45°) is 51.6, not 65.5. Plan answered Execute's refusal by assuming an
80.5 mm station "to place the left extreme at 0 mm"; 80.5 * cos(45°) is 56.9,
so the extreme landed at 8.58 and Execute refused again, in the same words. Two
of the run's three revisions went on a multiplication nobody performed, and the
Document stayed empty.

So Plan is told to write the sum down, and what it writes down is worked out
here — before the Plan Gate, where a wrong number is still cheap. Only what is
written as a sum is checked: this is not a geometry engine and it has no
opinion about a Step that states no arithmetic at all. A claim is
`<sum> = <number>`, where the left side is arithmetic and nothing else — no
words, so `Set Type = ThroughAll` and `the Length = 30` are not claims — and it
has to contain an operation, because `30 = 30` is a label rather than a sum
somebody had to do.

Trigonometry is in degrees, because a drawing is in degrees. `cos(45)` read the
way Python reads it is 0.5253, which is not a number on any sheet.
"""
import ast
import math
import re

_CLAIM = re.compile(
    r"(?P<sum>(?:(?:cos|sin|tan|sqrt)\s*\(|[0-9]+(?:\.[0-9]+)?|[-+*/^×÷()°]|\s|mm)+?)"
    r"=\s*(?P<answer>-?[0-9]+(?:\.[0-9]+)?)")

_AN_OPERATION = re.compile(r"[-+*/^×÷]|cos|sin|tan|sqrt")

_FUNCTIONS = {
    "cos": lambda value: math.cos(math.radians(value)),
    "sin": lambda value: math.sin(math.radians(value)),
    "tan": lambda value: math.tan(math.radians(value)),
    "sqrt": math.sqrt,
}


def wrong_sums(prose) -> list:
    """
    Every sum in *prose* that does not come out, one line each.

    Silent about everything else. A sum with a name in it, a division by zero, a
    power too big to evaluate — none of them is a Step that is wrong, and a Plan
    handed back over a sentence nobody can correct is worse than the sentence.
    """
    wrong = []
    for claim in _CLAIM.finditer(str(prose or "")):
        written = claim.group("sum").strip()
        if not _AN_OPERATION.search(written):
            continue
        comes_to = _worked_out(written)
        if comes_to is None:
            continue
        answer = float(claim.group("answer"))
        if _rounds_to(comes_to, claim.group("answer")):
            continue
        wrong.append("%s = %s, and it is %s" % (written, claim.group("answer"),
                                                _said(comes_to)))
    return wrong


def _worked_out(written):
    """What the sum comes to, or None where it is not a sum this can do."""
    try:
        return _value(ast.parse(_as_python(written), mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _as_python(written):
    for sign, means in (("×", "*"), ("÷", "/"), ("^", "**"), ("°", ""), ("mm", "")):
        written = written.replace(sign, means)
    return written


def _value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _value(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        return _OPERATIONS[type(node.op)](_value(node.left), _value(node.right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCTIONS and len(node.args) == 1 and not node.keywords:
        return _FUNCTIONS[node.func.id](_value(node.args[0]))
    raise ValueError("not a sum: " + ast.dump(node))


_OPERATIONS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.Pow: lambda left, right: left ** right,
}


def _rounds_to(comes_to, answer) -> bool:
    """
    Whether the answer written down is the sum at the precision it was written
    to. A Step is prose and a draughtsman rounds: 25 / 3 is 8.3 and it is 8.33.
    """
    _, _, decimals = answer.partition(".")
    return round(comes_to, len(decimals)) == float(answer)


def _said(comes_to):
    """The value, without the trailing zeros that make a sum look precise."""
    return ("%.2f" % comes_to).rstrip("0").rstrip(".")
