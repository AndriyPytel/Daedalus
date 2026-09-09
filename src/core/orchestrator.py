"""The routing orchestrator: the one call every user message passes through
before Design, a run question, or the run controller ever sees it.
"""
from core.events import ROUTED
from core.llm import TEXT, Session
from core.prompts import read_prompt

DESIGN = "design"
RUN_QUESTION = "run_question"
CONTROL = "control"

ROUTES = (DESIGN, RUN_QUESTION, CONTROL)

SYSTEM = read_prompt("orchestrator_system.md")


def classify(message: str, llm, session: Session = None) -> str:
    """
    One model call naming *message*'s route. The message reaches the model
    verbatim -- classification reads it, it does not rewrite it -- and the
    reply is read back for whichever of `ROUTES` it named, falling back to
    DESIGN when the model answers with none of them, since a design request
    is the route every phase already exists to serve.
    """
    session = session or Session(system=SYSTEM)
    said = "".join(chunk.text for chunk in session.turn(llm, message)
                   if chunk.kind == TEXT).strip().lower()
    for candidate in ROUTES:
        if candidate in said:
            return candidate
    return DESIGN


def route(message: str, *, llm, events, handlers: dict, session: Session = None):
    """
    Classify *message*, log the decision on *events* as its own entry
    separate from the message it names, then hand *message* to whichever
    callable `handlers` names for that route -- unchanged, alongside
    the decision itself, never paraphrased or reformatted.
    """
    decision = classify(message, llm, session=session)
    events.emit(ROUTED, route=decision)
    return handlers[decision](message, decision)
