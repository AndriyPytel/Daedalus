"""
The routing orchestrator: classification names one of three routes, the
event log gets the decision as its own entry, and the handler chosen for
that route receives the user's message exactly as typed.
"""
from core.events import ROUTED, EventStream
from core.llm import ScriptedLLMClient
from core.orchestrator import CONTROL, DESIGN, RUN_QUESTION, classify, route
from tests.event_helpers import events_of


def test_classifies_a_design_request():
    llm = ScriptedLLMClient(["design"])
    assert classify("make me a 10mm cube", llm) == DESIGN


def test_classifies_a_run_question():
    llm = ScriptedLLMClient(["run_question"])
    assert classify("is the run still going?", llm) == RUN_QUESTION


def test_classifies_a_control_instruction():
    llm = ScriptedLLMClient(["control"])
    assert classify("stop", llm) == CONTROL


def test_unrecognised_reply_falls_back_to_design():
    llm = ScriptedLLMClient(["I have no idea"])
    assert classify("???", llm) == DESIGN


def test_three_message_types_route_to_three_distinct_handlers():
    seen = {}

    def handler_for(name):
        def handler(message, decision):
            seen[name] = (message, decision)
            return name
        return handler

    handlers = {
        DESIGN: handler_for("design"),
        RUN_QUESTION: handler_for("run_question"),
        CONTROL: handler_for("control"),
    }

    for reply, expected in ((["design"], "design"),
                             (["run_question"], "run_question"),
                             (["control"], "control")):
        llm = ScriptedLLMClient(reply)
        events = EventStream()
        result = route("a message", llm=llm, events=events, handlers=handlers)
        assert result == expected

    assert set(seen) == {"design", "run_question", "control"}
    assert "design" not in seen["run_question"][0]
    assert seen["design"][1] == DESIGN
    assert seen["control"][1] == CONTROL


def test_routing_decision_is_its_own_event_separate_from_the_message():
    llm = ScriptedLLMClient(["design"])
    events = EventStream()
    handlers = {DESIGN: lambda message, decision: message,
                RUN_QUESTION: lambda message, decision: message,
                CONTROL: lambda message, decision: message}
    route("build a bracket", llm=llm, events=events, handlers=handlers)

    routed_events = events_of(events, ROUTED)
    assert len(routed_events) == 1
    assert routed_events[0].data == {"route": DESIGN}
    assert "build a bracket" not in [event.data.get("route") for event in routed_events]


def test_verbatim_preservation_with_unusual_whitespace():
    odd = "make  a   cube\n\twith   weird\t\tspacing  "
    received = {}

    def handler(message, decision):
        received["text"] = message
        return message

    llm = ScriptedLLMClient(["design"])
    events = EventStream()
    result = route(odd, llm=llm, events=events, handlers={DESIGN: handler,
                                                           RUN_QUESTION: handler,
                                                           CONTROL: handler})

    assert result == odd
    assert received["text"] == odd
