"""A function in `describe.py` that nothing in `src/` calls is a check that never runs."""
import ast
import glob
import os
import re

import core.loop.describe as describe_module

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(describe_module.__file__))))


def _defined():
    tree = ast.parse(open(describe_module.__file__).read())
    return [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]


def _text():
    return "\n".join(open(path).read() for path in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True))


def test_every_function_in_describe_has_a_caller_in_src():
    text = _text()
    unused = [name for name in _defined()
              if len(re.findall(r"(?<![\w.])%s\b|\.%s\b" % (name, name), text)) < 2]

    assert unused == []
