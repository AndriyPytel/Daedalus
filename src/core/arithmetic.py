"""
The sums a Step states about itself, worked out; a claim is
`<sum> = <number>`, and trigonometry is in degrees. Counter-example: a 73 mm
arm at 45 degrees claimed 65.5 mm, but 73 * cos(45 degrees) is 51.6, and two
of three revisions were spent on a multiplication nobody had performed while
the Document stayed empty.
"""
import ast
import math
import re

_ARITHMETIC = (r"(?:(?<![A-Za-z])(?:acos|asin|atan|cos|sin|tan|sqrt)\s*\("
               r"|[0-9]+(?:\.[0-9]+)?|[-+*/^×÷()°√]|\s|mm)")

_CLAIM = re.compile(r"(?:%s+?=\s*)+?-?[0-9]+(?:\.[0-9]+)?(?![0-9.])"
                    r"(?!\s*[-+*/^×÷]\s*-?[0-9])(?!%s*=)"
                    % (_ARITHMETIC, _ARITHMETIC))

_AN_OPERATION = re.compile(r"[-+*/^×÷√]|(?<![A-Za-z])(?:acos|asin|atan|cos|sin|tan|sqrt)")

_FUNCTIONS = {
    "cos": lambda value: math.cos(math.radians(value)),
    "sin": lambda value: math.sin(math.radians(value)),
    "tan": lambda value: math.tan(math.radians(value)),
    "sqrt": math.sqrt,
    "acos": lambda value: math.degrees(math.acos(value)),
    "asin": lambda value: math.degrees(math.asin(value)),
    "atan": lambda value: math.degrees(math.atan(value)),
}


def wrong_sums(prose) -> list:
    """
    Every sum in *prose* that does not come out, one line each, naming the
    first stretch that disagrees. Silent about everything else -- a named
    sum, a division by zero, or an oversized power is not a Step that is
    wrong, and a Plan handed back over a sentence nobody can correct is worse
    than the sentence. A chain is one claim however many `=` it is written
    with, each stretch worked out against the *last* number rather than the
    one it is written in front of, because a Plan refused for its punctuation
    once cost a recorded run the two revisions it had left.
    """
    wrong = []
    for claim in _CLAIM.finditer(str(prose or "")):
        written = [stretch.strip() for stretch in claim.group().split("=")]
        answer = written.pop()
        for sum_of_it in written:
            if not _AN_OPERATION.search(sum_of_it):
                continue
            comes_to = _worked_out(sum_of_it)
            if comes_to is None or _rounds_to(comes_to, answer):
                continue
            wrong.append("%s = %s, and it is %s" % (sum_of_it, answer, _said(comes_to)))
            break
    return wrong


def _worked_out(written):
    """What the sum comes to, or None where it is not a sum this can do."""
    try:
        return _value(ast.parse(_as_python(written), mode="eval").body)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _as_python(written):
    for sign, means in (("×", "*"), ("÷", "/"), ("^", "**"), ("°", ""), ("mm", ""),
                         ("√", "sqrt")):
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
