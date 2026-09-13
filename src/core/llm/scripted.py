"""Test double for the LLM client seam."""
from typing import Iterable, Iterator, List

from core.llm.types import REASONING, TEXT, Chunk


class ScriptedLLMClient:
    """
    The test double for the seam. Hand it the turns you want back, in order,
    and it records the messages it was asked with.

        client = ScriptedLLMClient(["a plan", {"reasoning": "hmm", "text": "code"}])
    """

    def __init__(self, turns: Iterable = ()):
        self.model = "scripted"
        self.turns: List = list(turns)
        self.calls: List[list] = []

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        self.calls.append(messages)
        if not self.turns:
            raise AssertionError(
                "ScriptedLLMClient ran out of turns on call %d" % len(self.calls)
            )
        return _replay(self.turns.pop(0), len(self.calls))


def _replay(turn, call: int) -> Iterator[Chunk]:
    """An Exception anywhere in a turn is raised there — a stream can die partway."""
    if isinstance(turn, Exception):
        raise turn
    for chunk in _as_chunks(turn, call):
        if isinstance(chunk, Exception):
            raise chunk
        yield chunk


def _as_chunks(turn, call: int) -> List[Chunk]:
    """
    *turn* as the Chunks it plays, or a refusal naming what it actually was.

    A list is only ever a turn already built as Chunk objects: a `_MultiTurn`
    or any other bare list landing here is a caller under-scripting a phase
    and a later call's turn being popped in its place, which a silent
    `list(turn)` once turned into raw strings read as Chunks three frames
    later, at whatever unlucky line first called `.kind` on one of them.
    """
    if isinstance(turn, str):
        return [Chunk(TEXT, turn)]
    if isinstance(turn, dict):
        chunks = []
        if turn.get("reasoning"):
            chunks.append(Chunk(REASONING, turn["reasoning"]))
        if turn.get("text"):
            chunks.append(Chunk(TEXT, turn["text"]))
        return chunks
    if isinstance(turn, (list, tuple)) and all(
            isinstance(item, (Chunk, Exception)) for item in turn):
        return list(turn)
    raise TypeError(
        "ScriptedLLMClient's turn for call %d is a %s, not a str, a dict or a "
        "list of Chunk: %r" % (call, type(turn).__name__, turn))
