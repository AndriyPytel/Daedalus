"""
Level 1 -- Kernel remembers the design it was given and resends it with
every request that hands over a document, including the resume after a
restart. `_ask` is the seam: no real FreeCAD process is needed to see what
the second request carries.
"""
import pytest

from core.kernel.kernel import Kernel


@pytest.fixture
def kernel(monkeypatch):
    instance = Kernel(freecadcmd="unused")
    monkeypatch.setattr(Kernel, "alive", property(lambda self: True))
    monkeypatch.setattr(instance, "start", lambda: None)
    monkeypatch.setattr(instance, "kill", lambda: None)
    return instance


def test_design_survives_a_restart(kernel, monkeypatch):
    requests = []

    def fake_ask(request, timeout, doing):
        requests.append(request)
        return {"ok": True, "checkpoint": "checkpoint-1"}, None

    monkeypatch.setattr(kernel, "_ask", fake_ask)

    kernel.check("addObject", document="doc.FCStd", design={"features": []})
    kernel.restart()
    kernel.check("assert design is not None")

    assert requests[1]["design"] == {"features": []}
