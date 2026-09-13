"""Every fan-out group shares one ScriptedLLMClient's turns on purpose; popping them must not race."""
import time
from concurrent.futures import ThreadPoolExecutor

from core.llm.scripted import ScriptedLLMClient


class _SlowToAdmitEmpty(list):
    """Widens the window between `stream`'s emptiness check and its pop, the way a real GIL switch rarely does on its own."""

    def __bool__(self):
        truthy = len(self) > 0
        time.sleep(0.02)
        return truthy


def test_racing_threads_pop_the_last_turn_without_raising_indexerror():
    """
    Four threads race one turn left. A thread that saw the list non-empty
    and lost the race to `pop(0)` must still be told plainly it ran out of
    turns -- not crash on `list.pop`'s own `IndexError`, which is the check
    finding stale truth, not the client running dry.
    """
    threads = 4
    client = ScriptedLLMClient(["a reply"])
    client.turns = _SlowToAdmitEmpty(client.turns)
    errors = []

    def call():
        try:
            list(client.stream([]))
        except AssertionError:
            pass
        except Exception as exc:
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(lambda _: call(), range(threads)))

    assert not errors, errors
