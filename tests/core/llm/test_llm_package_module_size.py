"""Guards the package split's own acceptance criterion — FCC-337.

Every module under `core/llm/` stays under the split's own ceiling, so the
concern it holds stays a single read.
"""
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "src", "core", "llm")


def test_no_llm_module_exceeds_500_lines():
    offenders = {}
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(SRC, name)
        with open(path, encoding="utf-8") as handle:
            length = sum(1 for _ in handle)
        if length > 500:
            offenders[name] = length

    assert not offenders, "over the 500-line budget: %s" % offenders
