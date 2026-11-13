"""
Level 1 -- the Kernel's public methods are its lifecycle and five verbs, and `measure` hands its script a file for facts too long for the kernel's output.
"""
import json
import os

from core.kernel import Kernel

LIFECYCLE = {"start", "kill", "restart", "alive", "pid"}
VERBS = {"rehearse", "probe", "seed", "render", "measure"}


def test_the_kernel_interface_is_five_verbs():
    public = {name for name in vars(Kernel) if not name.startswith("_")}

    assert public - LIFECYCLE == VERBS
    assert not hasattr(Kernel, "check") and not hasattr(Kernel, "reading")


def test_facts_too_long_for_the_kernels_output_are_read_from_the_file_named(monkeypatch):
    seen = []

    def ask(request, timeout, doing):
        namespace = {}
        exec(request["code"].split("\n")[0], namespace)
        seen.append(namespace["FACTS_PATH"])
        with open(namespace["FACTS_PATH"], "w") as handle:
            handle.write(json.dumps({"walls": ["x" * 9000]}))
        return {"ok": True, "stdout": "noise\nFACTS @\n"}, None

    kernel = Kernel(freecadcmd="unused")
    monkeypatch.setattr(kernel, "_ask", ask)

    assert kernel.measure("script") == {"walls": ["x" * 9000]}
    assert not os.path.exists(seen[0])


def test_a_script_that_prints_no_facts_measures_nothing(monkeypatch):
    kernel = Kernel(freecadcmd="unused")
    monkeypatch.setattr(kernel, "_ask", lambda *args: ({"ok": True, "stdout": "noise\n"}, None))

    assert kernel.measure("script") is None
