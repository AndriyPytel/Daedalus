"""
The routing orchestrator: the one call every user message passes through
before Design, a run question, or the run controller ever sees it. It names
what the message is; it never touches the words themselves, because FCC-154's
rule that Intent is exactly what the user asked for has to survive the trip
through this module too (epic FCC-291 Problem Statement).

It is one model call the user waits on directly, so it stays a single
question-and-answer turn -- classify, then hand off -- rather than a
conversation of its own; `Session` (FCC-309) is still what carries that one
turn, because a caller wiring the orchestrator into a run of several messages
gets the same persistent-history seam every other agent does.
"""
from core.events import ROUTED
from core.llm import TEXT, Session

DESIGN = "design"
RUN_QUESTION = "run_question"
CONTROL = "control"

ROUTES = (DESIGN, RUN_QUESTION, CONTROL)

_SYSTEM = (
    "You route a user's message to exactly one of three agents. Reply with "
    "one word and nothing else: `design` for a request to create or change "
    "the part, `run_question` for a question about a run already underway "
    "or already finished, `control` for an instruction to stop, abort, or "
    "otherwise control a running loop."
)


def classify(message: str, llm, session: Session = None) -> str:
    """
    One model call naming *message*'s route. The message reaches the model
    verbatim -- classification reads it, it does not rewrite it -- and the
    reply is read back for whichever of `ROUTES` it named, falling back to
    DESIGN when the model answers with none of them, since a design request
    is the route every phase already exists to serve.
    """
    session = session or Session(system=_SYSTEM)
    said = "".join(chunk.text for chunk in session.turn(llm, message)
                   if chunk.kind == TEXT).strip().lower()
    for candidate in ROUTES:
        if candidate in said:
            return candidate
    return DESIGN


def route(message: str, *, llm, events, handlers: dict, context=None, session: Session = None):
    """
    Classify *message*, log the decision on *events* as its own entry
    separate from the message it names, then hand *message* to whichever
    callable `handlers` names for that route -- unchanged, alongside
    *context* and the decision itself, never paraphrased or reformatted.
    """
    decision = classify(message, llm, session=session)
    events.emit(ROUTED, route=decision)
    return handlers[decision](message, decision, context)
