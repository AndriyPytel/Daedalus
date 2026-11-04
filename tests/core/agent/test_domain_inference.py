"""
Level 1 -- the Domain a run builds under: the Document's own Assembly decides first, one short model call decides otherwise, and the user's choice skips both.
"""
import pytest

from core.agent.domain import ASSEMBLY, PART
from core.agent.infer import AUTO, choose, classify, from_document, summary
from core.events import DOMAIN, EventStream
from core.llm import ScriptedLLMClient

ASSEMBLY_OBJECT = "Assembly::AssemblyObject"


def test_an_assembly_object_in_the_document_means_assembly():
    assert from_document(["Part::Feature", ASSEMBLY_OBJECT, "Assembly::JointGroup"]) == ASSEMBLY


def test_a_document_without_one_decides_nothing():
    assert from_document(["PartDesign::Body", "Sketcher::SketchObject"]) is None
    assert from_document([]) is None


def test_the_summary_counts_object_types():
    assert summary(["Part::Feature", "Part::Feature", "PartDesign::Body"]) == "Part::Feature x2, PartDesign::Body x1"
    assert summary([]) == "empty"


@pytest.mark.parametrize("answer, domain", [
    ("assembly", ASSEMBLY), ("Assembly.", ASSEMBLY), ("  ASSEMBLY\n", ASSEMBLY),
    ("part", PART), ("Part", PART),
    ("I am not sure", PART), ("", PART), ("an assembly of parts", PART),
])
def test_the_answer_is_parsed_and_an_unparseable_one_gives_part(answer, domain):
    assert classify("Join the plates.", [], ScriptedLLMClient([answer])) == domain


def test_the_call_carries_the_intent_and_the_document_summary_and_no_tools():
    llm = ScriptedLLMClient(["part"])

    classify("Make a bracket.", ["PartDesign::Body"], llm)

    (messages,) = llm.calls
    asked = "\n".join(str(m["content"]) for m in messages)
    assert "Make a bracket." in asked and "PartDesign::Body x1" in asked


def test_auto_with_an_assembly_in_the_document_makes_no_call():
    llm = ScriptedLLMClient([])
    events = EventStream()

    assert choose(AUTO, "Remove Part2.", [ASSEMBLY_OBJECT], llm, events) == ASSEMBLY

    assert llm.calls == []
    assert [e.data for e in events if e.kind == DOMAIN] == [{"domain": ASSEMBLY, "inferred": True}]


def test_auto_otherwise_asks_the_model_once():
    llm = ScriptedLLMClient(["assembly"])
    events = EventStream()

    assert choose(AUTO, "Join these three parts.", [], llm, events) == ASSEMBLY

    assert len(llm.calls) == 1
    assert [e.data for e in events if e.kind == DOMAIN] == [{"domain": ASSEMBLY, "inferred": True}]


@pytest.mark.parametrize("picked", [PART, ASSEMBLY])
def test_an_override_makes_no_call_and_says_so(picked):
    llm = ScriptedLLMClient([])
    events = EventStream()

    assert choose(picked, "Join these three parts.", [ASSEMBLY_OBJECT], llm, events) == picked

    assert llm.calls == []
    assert [e.data for e in events if e.kind == DOMAIN] == [{"domain": picked, "inferred": False}]


def test_the_call_asks_for_the_lowest_effort_and_leaves_the_session_client_alone():
    from core.llm import EFFORTS, TEXT, Chunk

    class Client:
        effort = EFFORTS[-1]
        asked_with = None

        def stream(self, messages, **overrides):
            Client.asked_with = self.effort
            return iter([Chunk(TEXT, "part")])

    client = Client()

    classify("Make a bracket.", [], client)

    assert Client.asked_with == EFFORTS[0]
    assert client.effort == EFFORTS[-1]


def test_the_call_is_paid_for_on_the_stream():
    from core.events import COST
    from core.llm import TEXT, Chunk

    class Client:
        effort = None
        last_cost = None

        def stream(self, messages, **overrides):
            self.last_cost = {"usd": 0.005, "model": "m", "prompt_tokens": 40}
            return iter([Chunk(TEXT, "part")])

    events = EventStream()

    choose(AUTO, "Make a bracket.", [], Client(), events)

    (paid,) = [e.data for e in events if e.kind == COST]
    assert paid["phase"] == "domain" and paid["usd"] == 0.005 and paid["prompt_tokens"] == 40


def test_no_call_means_no_cost_event():
    from core.events import COST

    events = EventStream()

    choose(PART, "Make a bracket.", [], ScriptedLLMClient([]), events)
    choose(AUTO, "Remove Part2.", [ASSEMBLY_OBJECT], ScriptedLLMClient([]), events)

    assert [e for e in events if e.kind == COST] == []


def test_a_value_that_is_not_a_choice_is_not_inferred():
    events = EventStream()

    assert choose("bim", "Make a bracket.", [], ScriptedLLMClient([]), events) == "bim"
    assert [e.data for e in events if e.kind == DOMAIN] == [{"domain": "bim", "inferred": False}]
