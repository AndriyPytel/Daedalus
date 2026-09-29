"""
Level 1 -- the composed system prompt two consecutive Execute calls of one
pass send, and why it has to be a byte-stable growing prefix for a provider's
own cache to carry the earlier call's cost into the later one.
"""
import json

from core.agent import phase_contract as pc
from core.agent import run_phase
from core.agent.agent import _composed_system
from core.kernel import KernelResult
from core.llm import ScriptedLLMClient
from core.llm.claude_stream import _system_of, _user_turn


def test_a_later_calls_system_prompt_carries_the_earlier_one_as_its_prefix():
    """
    The first Execute call of a pass sends no ledger; a Step that fails and
    is retried by a fresh call sends one. The two must still share a whole,
    unchanged prefix, or a provider reading its own cache off the request's
    leading bytes pays for the whole conversation on the second call too.
    """
    first = _composed_system("Write the next Step.")
    second = _composed_system("Write the next Step.",
                               ledger="- Cut the hooks: moved no material")

    assert second.startswith(first)
    assert second != first


def test_two_consecutive_execute_calls_of_one_pass_send_the_same_prefix(tmp_path):
    """
    A run through exploration, a Step that fails preflight, a Step that
    lands and another Step, driven with the same `run_phase` one Execute
    call actually uses -- the request `core.llm.claude_stream` would build
    for the CLI route on every one of those calls has to start with the
    request built for the call before it, or a provider reading its own
    cache off the leading bytes pays for the whole conversation again.
    """
    class SequencedKernel:
        def __init__(self, results):
            self._results = list(results)

        def check(self, code, **kwargs):
            return self._results.pop(0)

    checkpoint_1 = tmp_path / "001.FCStd"
    checkpoint_1.write_text("not really an FCStd")
    checkpoint_2 = tmp_path / "002.FCStd"
    checkpoint_2.write_text("not really an FCStd")

    explored = KernelResult(ok=True)
    fail_1 = KernelResult(ok=False, traceback="AssertionError: Step 1 moved no material")
    land_1 = KernelResult(ok=True, checkpoint=str(checkpoint_1))
    fail_2 = KernelResult(ok=False, traceback="AssertionError: Step 2 self-intersects")
    land_2 = KernelResult(ok=True, checkpoint=str(checkpoint_2))

    llm = ScriptedLLMClient([
        json.dumps({"code": "print(doc.Objects)"}),
        json.dumps({"code": "box_1_bad()", "step": 1}),
        json.dumps({"code": "box_1_good()", "step": 1}),
        json.dumps({"code": "box_2_bad()", "step": 2}),
        json.dumps({"code": "box_2_good()", "step": 2}),
        json.dumps({"declare": {"steps": [{"step": 1}, {"step": 2}]}}),
    ])

    run_phase(
        pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad two boxes",
        kernel=SequencedKernel([explored, fail_1, land_1, fail_2, land_2]),
        on_declaration=lambda declaration: {"delivered": True},
    )

    systems = [_system_of(messages) for messages in llm.calls]
    turns = [[{key: value for key, value in block.items() if key != "cache_control"}
              for block in _user_turn(messages)["message"]["content"]]
             for messages in llm.calls]

    for index, (earlier, later) in enumerate(zip(systems, systems[1:])):
        assert later.startswith(earlier), \
            "call %d's system prompt is not a prefix of call %d's" % (index, index + 1)
    for index, (earlier, later) in enumerate(zip(turns, turns[1:])):
        assert later[:len(earlier)] == earlier, \
            "call %d's user turn is not a prefix of call %d's" % (index, index + 1)

    assert any(block["text"].startswith("Your reply:") for block in turns[-1]), \
        "a later call carries none of the model's own earlier replies"


def test_only_the_conversations_last_block_carries_a_cache_breakpoint():
    """
    The `claude` CLI spends 3 of Anthropic's 4 cache breakpoints on its own
    and, on Sonnet, none of them at the end of the conversation, so the one
    this project may place goes on the last block: each call then reads what
    the call before it wrote instead of rewriting everything past the task.
    """
    messages = [
        {"role": "system", "content": "You write FreeCAD Python."},
        {"role": "user", "content": "Make a 10mm plate"},
        {"role": "assistant", "content": "box_1_good()"},
        {"role": "user", "content": "Step 1 self-intersects"},
    ]

    turn = _user_turn(messages)["message"]["content"]

    assert turn[-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert all("cache_control" not in block for block in turn[:-1])
