"""
What an event says to a person, said once in `core.events`' table rather than in
the Qt panel's and `run.py`'s own separate chains. What is left to each
narrator is where a line goes: a chat bubble, a collapsible row, a status line.
"""
from core.events import REGISTRY, Narration

_NOTHING = Narration()


def narrate(event) -> Narration:
    """What *event* says; an unregistered kind says nothing rather than raising."""
    kind = REGISTRY.get(event.kind)
    if kind is None or kind.narration is None:
        return _NOTHING
    return kind.narration(event.data)
