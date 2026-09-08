"""
`config/prompts/plan.md`'s closed `op` vocabulary (FCC-233, ADR-0026,
`FCC-A-52`).

The vocabulary is read out of `config/prompts/plan.md` itself, never copied
into this file — the same rule `test_vocabulary_closure.py` applies to
`design.md`'s closed `kind` list, for the same reason: a test carrying its
own copy of the closed list passes even while the prompt says something
else.
"""
import os
import re

PLAN_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "prompts", "plan.md")
LANGUAGE_MD = os.path.join(os.path.dirname(PLAN_MD), "language.md")


def _plan_md():
    with open(PLAN_MD, encoding="utf-8") as f:
        return f.read()


def _language_md():
    with open(LANGUAGE_MD, encoding="utf-8") as f:
        return f.read()


def _op_section(text):
    """
    The closed `op` vocabulary. It lives in `language.md` now (ADR-0026,
    FCC-234), composed into `plan.md` by `core.loop.prompt` rather than
    written there a second time.
    """
    start = text.index("## The operation is one of 36")
    end = text.index("## A Step is one sentence")
    return text[start:end]


def _op_vocabulary(section):
    listed = re.search(r"one of \(([^)]+)\)", section)
    return [w.strip() for w in listed.group(1).split(",")]


def test_the_op_vocabulary_is_36_entries_with_no_other_escape_hatch():
    ops = _op_vocabulary(_op_section(_language_md()))

    assert len(ops) == 36
    assert "other" not in ops


def test_the_marker_and_the_op_field_are_both_in_the_reply_shape():
    text = _plan_md()

    assert '"op_vocabulary": "closed"' in text
    assert '"op": "..."' in text


def test_core_loop_op_verbs_matches_the_prompts_own_list():
    """
    `core.loop.OP_VERBS` is the closed list `_steps_of` refuses an `op`
    against (FCC-233); this prompt is what asks the model for it. Two
    independent copies of a closed list is exactly the shape that drifts
    silently, so a test keeps them equal rather than a comment promising it.
    """
    from core.loop.plan import OP_VERBS

    ops = _op_vocabulary(_op_section(_language_md()))

    assert tuple(ops) == OP_VERBS


EXECUTE_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "prompts", "execute.md")


def _execute_md():
    with open(EXECUTE_MD, encoding="utf-8") as f:
        return f.read()


def test_every_op_typeid_lint_checks_is_named_in_execute_md():
    """
    `execute.md` is what tells the model which TypeId to build for a given
    `op`; `core.lint.OP_TYPEIDS` is what checks the code it wrote against
    that same mapping. A TypeId lint accepts that the prompt never mentions
    is one Execute can never be told to write, which fails the Step at lint
    for a reason the model was never given a chance to satisfy.

    The 16 primitive TypeIds (`additive-primitive`/`subtractive-primitive`,
    8 shapes each) are named in the prompt as `PartDesign::Additive<Shape>`
    and `PartDesign::Subtractive<Shape>` rather than spelled out individually
    — checked here as the prefix plus every shape name, not the literal
    concatenation `core.lint` builds.

    `additive-helix`'s TypeId is exempt: which type this build offers for a
    thread is knowledge about a specific FreeCAD build, so it belongs in
    TRAPS and never as a literal string in a prompt file (FCC-58,
    `test_the_curve_and_thread_types_are_traps_not_prompt_copy`).
    """
    from core.lint import _PRIMITIVE_SHAPES, OP_TYPEIDS

    text = _execute_md()
    primitive_ops = {"additive-primitive", "subtractive-primitive"}
    build_specific = {"additive-helix"}
    missing = [typeid for op, typeids in OP_TYPEIDS.items()
               if op not in primitive_ops | build_specific
               for typeid in typeids if typeid not in text]

    assert not missing, missing
    assert "additive-helix" in text, \
        "additive-helix must still be named, even where its TypeId is a trap (FCC-58)"
    assert "PartDesign::Additive<Shape>" in text
    assert "PartDesign::Subtractive<Shape>" in text
    missing_shapes = [shape for shape in _PRIMITIVE_SHAPES if shape not in text]
    assert not missing_shapes, missing_shapes


def test_every_op_verb_is_named_in_execute_md():
    from core.loop.plan import OP_VERBS

    text = _execute_md()
    missing = [op for op in OP_VERBS if ("`%s`" % op) not in text]

    assert not missing, missing
