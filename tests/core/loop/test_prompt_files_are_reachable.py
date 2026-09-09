"""
Every file under `config/prompts/` is reachable from code, and every name a
caller asks `read_prompt` for exists (FCC-346).

`core.loop.prompt.prompt()` resolves `phase + ".md"` rather than a literal,
so the phase names it can ask for are read off `_PHASE_VOCABULARY`'s own
keys -- the same dict `prompt()` composes from -- rather than retyped here.
Every other call site names its file directly and is found by walking the
AST for `read_prompt("...")`.
"""
import ast
import os

from core.loop.prompt import _PHASE_VOCABULARY
from core.prompts import ROOT
from source_tree import sources


def _literal_read_prompt_calls():
    names = set()
    for _path, text in sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "read_prompt"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                names.add(node.args[0].value)
    return names


def _files_on_disk():
    found = set()
    for _dirpath, _dirs, names in os.walk(ROOT):
        found.update(names)
    return found


def test_every_prompt_file_is_reachable_and_every_name_asked_for_exists():
    asked_for = _literal_read_prompt_calls() | {key + ".md" for key in _PHASE_VOCABULARY}
    on_disk = _files_on_disk()
    assert asked_for == on_disk
