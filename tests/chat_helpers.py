import json

import pytest

from core import settings
from core.kernel import Kernel, find_freecadcmd
from core.llm import ClaudeCodeClient, TEXT, Chunk


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def set_the_chat(monkeypatch, model, level, replies):
    """
    Writes the two settings the chat's pills write, and makes the client built from them answer from *replies*.

    Returns the list that records the model and Effort each call was made with.
    """
    settings.instance().set_last_model("Claude (subscription)", model)
    settings.instance().set_effort(level)
    asked = []
    script = iter(replies)

    def stream(self, messages, **overrides):
        asked.append((self.model, self.effort))
        yield Chunk(TEXT, next(script))

    monkeypatch.setattr(ClaudeCodeClient, "stream", stream)
    return asked


def say(**turn):
    return json.dumps(turn)
