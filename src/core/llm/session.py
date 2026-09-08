"""One agent's conversation with an LLM."""
import uuid
from typing import Iterator, List

from core.llm.types import TEXT, Chunk


class Session:
    """
    One agent's conversation: a persistent message list, kept and handed back
    whole on every `turn`, so a second call to the same agent sees what it
    said and was told on the first without the caller reassembling
    `[system, user] + prior` itself.
    """

    def __init__(self, id: str = None, system: str = None):
        self.id = id or uuid.uuid4().hex
        self.messages: List[dict] = []
        if system:
            self.messages.append({"role": "system", "content": system})

    def turn(self, llm, content) -> Iterator[Chunk]:
        """
        Stream a reply to *content*, added as this conversation's next user
        turn. The reply is appended to `messages` too, once the stream is
        drained, so the conversation *llm* sees on the next `turn` is the
        whole history rather than one call's worth of it.
        """
        self.messages.append({"role": "user", "content": content})
        text = []
        for chunk in llm.stream(list(self.messages)):
            if chunk.kind == TEXT:
                text.append(chunk.text)
            yield chunk
        self.messages.append({"role": "assistant", "content": "".join(text)})
