"""
The tree-wide sub-agent supervisor -- delegation, and the
turn/token budgets it enforces (wall time stays telemetry).
"""
import pytest

from core.agent import subagent as sa
from core.llm import ScriptedLLMClient, Session


def test_a_phase_agent_may_spawn_a_worker():
    supervisor = sa.SessionTreeSupervisor()
    assert supervisor.spawn() is True
    assert supervisor.refusals == []


def test_a_run_over_the_turn_budget_is_budget_exhausted():
    supervisor = sa.SessionTreeSupervisor(turn_budget=3)
    for _ in range(3):
        supervisor.record_turn()
    assert supervisor.outcome is None
    supervisor.record_turn()
    assert supervisor.outcome == sa.BUDGET_EXHAUSTED


def test_a_run_over_the_token_budget_is_budget_exhausted():
    supervisor = sa.SessionTreeSupervisor(token_budget=100)
    supervisor.record_tokens(100)
    assert supervisor.outcome is None
    supervisor.record_tokens(1)
    assert supervisor.outcome == sa.BUDGET_EXHAUSTED


def test_a_spawn_once_budget_exhausted_is_refused_too():
    supervisor = sa.SessionTreeSupervisor(turn_budget=1)
    supervisor.record_turn()
    supervisor.record_turn()
    assert supervisor.spawn() is False
    assert supervisor.refusals[-1] == {"reason": sa.BUDGET_EXHAUSTED}


def test_delegate_returns_only_the_answer_worker_history_is_not_merged():
    phase = Session()
    worker_llm = ScriptedLLMClient(["worker's private reasoning then an answer"])
    supervisor = sa.SessionTreeSupervisor()

    answer = sa.delegate(supervisor, phase, llm=worker_llm,
                          prompt="delegate this")

    assert answer == "worker's private reasoning then an answer"
    assert phase.messages[-1] == {"role": "assistant", "content": answer}
    assert len(phase.messages) == 1


def test_delegate_refused_over_budget_returns_none_and_touches_no_session():
    phase = Session()
    worker_llm = ScriptedLLMClient(["should never be asked"])
    supervisor = sa.SessionTreeSupervisor(turn_budget=0)
    supervisor.record_turn()

    answer = sa.delegate(supervisor, phase, llm=worker_llm,
                          prompt="a worker trying to delegate further")

    assert answer is None
    assert phase.messages == []
    assert worker_llm.calls == []


def test_delegate_emits_spawned_then_answered_on_the_stream():
    from core.events import EventStream, SUB_AGENT_ANSWERED, SUB_AGENT_SPAWNED

    phase = Session()
    worker_llm = ScriptedLLMClient(["the answer"])
    supervisor = sa.SessionTreeSupervisor()
    stream = EventStream()

    sa.delegate(supervisor, phase, llm=worker_llm,
                prompt="delegate this", stream=stream, phase="design")

    kinds = [event.kind for event in stream]
    assert kinds == [SUB_AGENT_SPAWNED, SUB_AGENT_ANSWERED]
    assert list(stream)[0].data == {"phase": "design", "prompt": "delegate this"}
    assert list(stream)[1].data["phase"] == "design"
    assert list(stream)[1].data["answer"] == "the answer"
    assert list(stream)[1].data["transcript"] == worker_llm.calls[0] + [
        {"role": "assistant", "content": "the answer"}]


def test_delegate_emits_nothing_without_a_stream():
    supervisor = sa.SessionTreeSupervisor()
    worker_llm = ScriptedLLMClient(["the answer"])
    phase = Session()

    answer = sa.delegate(supervisor, phase, llm=worker_llm, prompt="x")

    assert answer == "the answer"


def test_delegate_records_the_tokens_a_turn_spent_and_a_run_over_budget_is_refused():
    class _PricedLLM(ScriptedLLMClient):
        def __init__(self, turns, prompt_tokens, completion_tokens):
            super().__init__(turns)
            self.last_cost = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}

    phase = Session()
    worker_llm = _PricedLLM(["the answer"], prompt_tokens=61, completion_tokens=40)
    supervisor = sa.SessionTreeSupervisor(token_budget=100)

    sa.delegate(supervisor, phase, llm=worker_llm, prompt="delegate this")

    assert supervisor.tokens == 101
    assert supervisor.outcome == sa.BUDGET_EXHAUSTED
    assert supervisor.spawn() is False
    assert supervisor.refusals[-1] == {"reason": sa.BUDGET_EXHAUSTED}


def test_a_refused_delegate_emits_sub_agent_refused_and_calls_the_worker_never():
    from core.events import EventStream, SUB_AGENT_REFUSED

    phase = Session()
    worker_llm = ScriptedLLMClient(["should never be asked"])
    supervisor = sa.SessionTreeSupervisor(turn_budget=0)
    supervisor.record_turn()
    stream = EventStream()

    answer = sa.delegate(supervisor, phase, llm=worker_llm,
                          prompt="a worker trying to delegate further",
                          stream=stream, phase="worker")

    assert answer is None
    assert worker_llm.calls == []
    assert [event.kind for event in stream] == [SUB_AGENT_REFUSED]
    assert list(stream)[0].data == {"phase": "worker", "reason": sa.BUDGET_EXHAUSTED}


def test_exhaustion_reason_fires_once_no_matter_how_many_times_asked():
    """
    One supervisor, shared across every phase continuation of a run, must
    tell its own exhaustion once -- a caller asking again after the ceiling
    already fired gets `None`, not a second telling of the same event.
    """
    supervisor = sa.SessionTreeSupervisor(turn_budget=2)
    supervisor.record_turn()
    assert supervisor.exhaustion_reason() is None
    supervisor.record_turn()
    supervisor.record_turn()

    first = supervisor.exhaustion_reason()
    assert first == {"turns": 3, "budget": 2, "scope": "run"}
    assert supervisor.exhaustion_reason() is None

    supervisor.record_turn()
    assert supervisor.exhaustion_reason() is None, (
        "a supervisor already exhausted keeps telling nothing on later turns, "
        "not a fresh event per continuation")
