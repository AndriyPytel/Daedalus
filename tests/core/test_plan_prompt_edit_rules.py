"""Rules Plan is given only on an edit."""
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop.execute import execute
from core.loop.prompt import prompt
from tests.loop_helpers import FakeTools, StubKernel, code_reply

RULE = "is removed by the region it occupies, and that region is named by the base faces that bound it"


def test_plan_is_told_how_to_remove_a_feature_a_sketch_cannot_cut_only_on_an_edit():
    editing = " ".join(prompt("plan", editing=True).split())
    building = " ".join(prompt("plan").split())

    assert RULE in editing and "a PolarPattern cannot copy what exists only inside BaseFeature" in editing
    assert RULE not in building


BUILT = "A region a Step bounds by faces of the base is built in code from those faces' own surfaces"


def test_execute_is_told_to_build_a_region_from_the_bases_own_surfaces_only_on_an_edit():
    assert BUILT in " ".join(prompt("execute", editing=True).split())
    assert BUILT not in " ".join(prompt("execute").split())

    llm = ScriptedLLMClient(code_reply("pad()"))
    execute(["Cut the region"], FakeTools().registry, llm, EventStream(), kernel=StubKernel(), base="x")
    assert BUILT in " ".join(llm.calls[0][0]["content"].split())
