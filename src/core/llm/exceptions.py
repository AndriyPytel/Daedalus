"""Exceptions for the LLM client seam."""


class ProviderRefused(Exception):
    """
    The provider would not answer: a limit, a rejected key, a dropped
    connection, a reply cut off before it finished.

    Carries a sentence written for the user — which model, which limit, what to
    do about it — and `said`, what the provider itself replied, verbatim.
    """

    def __init__(self, message: str, said: str = ""):
        super().__init__(message)
        self.said = said


class StreamCutOff(ProviderRefused):
    """
    The provider closed the stream with no terminal chunk: no
    finish_reason, no usage, no `[DONE]`, and no exception either, the
    reply simply stops. Measured against the opencode gateway with raw
    HTTP and no LiteLLM in the call at all: of seven long streams four
    closed this way, after 5 to 100 seconds of output, while three ran 45
    to 180 seconds to a clean finish, so this is the connection rather
    than the model, with no duration to stay under. Retryable, which is
    the only reason it is not a plain `ProviderRefused`: the same call
    sent again usually lands, and retrying is what opencode's own client
    does with it.
    """
