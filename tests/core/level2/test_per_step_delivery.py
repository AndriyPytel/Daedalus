"""
FCC-368: a Step reaches the Document as it is declared, not once the whole
phase is over -- `on_declaration` fires, and answers, before the next turn's
code is ever asked for (ADR-0027, FCC-A-53).
"""
import json

import pytest

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


class RecordingLLMClient(ScriptedLLMClient):
    """Marks *order* the moment a turn is asked for, alongside on_declaration's
    own marks -- the interleaving is the whole point, not either alone."""

    def __init__(self, turns, order):
        super().__init__(turns)
        self.order = order

    def stream(self, messages, **overrides):
        self.order.append("asked")
        return super().stream(messages, **overrides)


def test_step_one_is_delivered_before_step_twos_code_is_ever_asked_for(kernel, tmp_path):
    """
    AC1: replayed as it is declared, not once Execute returns. A version that
    batches every delivery for the end of the phase asks for every Step's
    code before it delivers any of them -- this order distinguishes the two.
    """
    order = []
    llm = RecordingLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box2'); doc.recompute()",
                    "step": 2, "label": "second box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box"}]}}),
    ], order)

    def on_declaration(declaration):
        order.append("delivered:%s" % declaration["step"])
        return {"delivered": True}

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
             kernel=kernel, on_declaration=on_declaration)

    assert order == ["asked", "delivered:1", "asked", "delivered:2", "asked"], (
        "Step 2's code was asked for before Step 1 was delivered, or Step 1 "
        "was delivered only after every Step's code had already been asked for"
    )


def test_a_step_the_gui_could_not_replay_reverts_the_kernel_to_the_last_landed_checkpoint(
    kernel, tmp_path):
    """
    AC-drift: a GUI replay failure is drift between the kernel and the GUI
    Document, not a reason to abandon the phase or start Plan over. The
    kernel's live Document is wound back to the last Step that did land, and
    the model is asked to write the drifted Step again -- on the same turn
    loop, against a Document that still only has what landed.
    """
    marker = tmp_path / "seen_at_retry.txt"
    first_step2_code = "doc.addObject('Part::Box', 'Box2'); doc.recompute()"
    retry_step2_code = (
        "open(%r, 'w').write(str(len(doc.Objects)))\n"
        "doc.addObject('Part::Box', 'Box2Retry'); doc.recompute()\n"
    ) % str(marker)
    attempts = {"count": 0}

    def on_declaration(declaration):
        if declaration["step"] != 2:
            return {"delivered": True}
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"delivered": False, "drift": True, "traceback": "signature mismatch"}
        return {"delivered": True}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": first_step2_code, "step": 2, "label": "second box"}),
        json.dumps({"code": retry_step2_code, "step": 2, "label": "second box retried"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box retried"}]}}),
    ])

    _, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
        kernel=kernel, on_declaration=on_declaration,
    )

    assert [d["step"] for d in declarations] == [1, 2]
    assert [d["label"] for d in declarations] == ["first box", "second box retried"]
    assert marker.read_text() == "1", (
        "Box2's drifted attempt was still in the kernel's live Document when "
        "the retry ran -- the kernel was never wound back to Step 1's checkpoint"
    )


def test_a_step_the_document_refused_for_a_non_drift_reason_also_reverts_and_retries_in_place(
    kernel, tmp_path):
    """
    FCC-372: any refusal the Document gives at declare time -- an ownership
    guard, a lint mismatch, not only a GUI signature drift -- rewinds the
    kernel to the last landed checkpoint and asks for the Step again in this
    same session, the same as drift does.
    """
    marker = tmp_path / "seen_at_retry.txt"
    first_step2_code = "doc.addObject('Part::Box', 'Box2'); doc.recompute()"
    retry_step2_code = (
        "open(%r, 'w').write(str(len(doc.Objects)))\n"
        "doc.addObject('Part::Box', 'Box2Retry'); doc.recompute()\n"
    ) % str(marker)
    attempts = {"count": 0}

    def on_declaration(declaration):
        if declaration["step"] != 2:
            return {"delivered": True}
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"delivered": False, "retry": True, "traceback": "ownership: not this Body"}
        return {"delivered": True}

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box1'); doc.recompute()",
                    "step": 1, "label": "first box"}),
        json.dumps({"code": first_step2_code, "step": 2, "label": "second box"}),
        json.dumps({"code": retry_step2_code, "step": 2, "label": "second box retried"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "first box"},
                                          {"step": 2, "label": "second box retried"}]}}),
    ])

    _, _, declarations = run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
        kernel=kernel, on_declaration=on_declaration,
    )

    assert [d["step"] for d in declarations] == [1, 2]
    assert [d["label"] for d in declarations] == ["first box", "second box retried"]
    assert marker.read_text() == "1", (
        "Box2's refused attempt was still in the kernel's live Document when "
        "the retry ran -- the kernel was never wound back to Step 1's checkpoint"
    )
