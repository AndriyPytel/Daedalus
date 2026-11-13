"""
Level 1 — the one table of event kinds.

A kind is declared once in `core.events.REGISTRY`; the emitters under `src/`
are scanned here, so a kind emitted and not registered, or registered and
emitted by nobody, fails by name.
"""
import ast
import os

from core import events
from core.events import KINDS, REGISTRY

SRC = os.path.dirname(os.path.dirname(os.path.abspath(events.__file__)))


def _the_kinds_emitted_under_src():
    named = {name: value for name, value in vars(events).items()
             if name.isupper() and isinstance(value, str)}
    emitted = {}
    for folder, _, files in os.walk(SRC):
        for file in files:
            if not file.endswith(".py") or os.path.join(folder, file) == events.__file__:
                continue
            path = os.path.join(folder, file)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("emit", "_narrate") and node.args):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    emitted.setdefault(first.value, set()).add(path)
                elif isinstance(first, ast.Name) and first.id in named:
                    emitted.setdefault(named[first.id], set()).add(path)
    return emitted


def test_every_registered_kind_has_a_narration_or_says_it_is_structural():
    for name, kind in REGISTRY.items():
        assert kind.name == name
        assert kind.narration is not None or kind.structural, name


def test_every_kind_a_module_emits_is_registered():
    emitted = _the_kinds_emitted_under_src()

    assert set(emitted) <= set(KINDS), {
        name: sorted(paths) for name, paths in emitted.items() if name not in KINDS}
    assert set(KINDS) <= set(emitted), "registered and emitted by no module: %s" % \
        sorted(set(KINDS) - set(emitted))
    print("kinds emitted under src: %d" % len(emitted))
