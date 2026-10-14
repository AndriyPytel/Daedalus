"""
On an edit the Design and Plan phases' code reads the base part's addresses as `base_faces`, in the kernel's namespace; Execute and a generation run never get it.
"""
import json

from core.agent import agent as agent_module
from core.kernel import KernelResult
from core.agent import phase_contract as pc
from tests.loop_helpers import design_reply, drive, plan_reply

BASE = "The part before the edit, in mm:\nplanes:\n  plane y = -17.5 (area 1605.35 mm2)"
FACES = {"plane y = -17.5": 1605.35, "plane y = -177.5": 29511.0}
GOOD = {"features": [{"id": "boss", "edit": "changed", "at": ["plane y = -17.5"]}]}
BAD = {"features": [{"id": "boss", "edit": "changed", "at": ["the bore axis"]}]}
LOOK = json.dumps({"code": "print(sorted(base_faces)[:3])"})


class _Spy:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def check(self, code, **kwargs):
        _Spy.calls.append(dict(kwargs, code=code))
        return KernelResult(ok=True, stdout="[]")

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _run(monkeypatch, turns, edit=True):
    _Spy.calls = []
    monkeypatch.setattr(agent_module, "Kernel", _Spy)
    return drive(turns, base=BASE if edit else None,
                 faces=[{"base": FACES, "now": []}] * 4 if edit else (),
                 kernel=_Spy())


def _seen(code):
    return [call.get("base_faces") for call in _Spy.calls if call["code"] == code]


def test_design_code_on_an_edit_gets_the_base_faces(monkeypatch):
    _run(monkeypatch, [LOOK, design_reply(GOOD), plan_reply("Pad it")])

    assert _seen(json.loads(LOOK)["code"])[0] == FACES


def test_a_design_re_ask_gets_them_too(monkeypatch):
    _run(monkeypatch, [design_reply(BAD), LOOK, design_reply(GOOD), plan_reply("Pad it")])

    assert _seen(json.loads(LOOK)["code"]) == [FACES]


def test_plan_code_on_an_edit_gets_the_base_faces(monkeypatch):
    _run(monkeypatch, [design_reply(GOOD), LOOK, plan_reply("Pad it")])

    assert _seen(json.loads(LOOK)["code"])[0] == FACES


def test_a_generation_run_gets_no_base_faces(monkeypatch):
    _run(monkeypatch, [LOOK, design_reply({"envelope": {"length": {"value": "10 mm", "flag": "read"}}}),
                       plan_reply("Pad it")], edit=False)

    assert _seen(json.loads(LOOK)["code"]) == [None]


def test_the_run_reads_the_faces_off_the_tools_once_a_base_part_exists():
    from tests.loop_helpers import FakeTools

    tools = FakeTools(base="planes:\n  plane z = 10 (area 100 mm2)", faces=[{"base": FACES, "now": []}])

    with pc.the_base_faces_of(tools.registry):
        assert pc.BASE_FACES.get() == FACES
    assert pc.BASE_FACES.get() is None
    with pc.the_base_faces_of(FakeTools().registry):
        assert pc.BASE_FACES.get() is None
