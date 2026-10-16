"""
Level 1 -- Kernel remembers Plan's frozen values the same way it remembers
design, resending them with every request that hands over a document,
including the resume after a restart. `_ask` is the seam: no real FreeCAD
process is needed to see what the second request carries.
"""
import pytest

from core.kernel.kernel import Kernel, KernelResult


@pytest.fixture
def kernel(monkeypatch):
    instance = Kernel(freecadcmd="unused")
    monkeypatch.setattr(Kernel, "alive", property(lambda self: True))
    monkeypatch.setattr(instance, "start", lambda: None)
    monkeypatch.setattr(instance, "kill", lambda: None)
    return instance


def test_kernel_plan_values_property_reflects_the_last_response(kernel, monkeypatch):
    def fake_ask(request, timeout, doing):
        return {"ok": True, "checkpoint": "checkpoint-1",
                "plan_values": {"arm_y": 46.458, "flange_z": 12.0}}, None

    monkeypatch.setattr(kernel, "_ask", fake_ask)

    kernel.check("arm_y = 46.458", capture_values=True)

    assert kernel.plan_values == {"arm_y": 46.458, "flange_z": 12.0}


def test_a_result_carries_plan_values_off_the_wire():
    result = KernelResult.from_response(
        {"ok": True, "checkpoint": "c1", "plan_values": {"arm_y": 46.458}})

    assert result.plan_values == {"arm_y": 46.458}
