"""Basic types for the LLM client seam."""
from dataclasses import dataclass

REASONING = "reasoning"
TEXT = "text"


@dataclass(frozen=True)
class Chunk:
    """One piece of a streamed reply. `kind` is REASONING or TEXT."""
    kind: str
    text: str
