"""The Plan Gate: approval, revision decisions, and user questions."""
import re
from dataclasses import dataclass



APPROVE = "approve"


REJECT = "reject"


AMEND = "amend"


@dataclass
class Approval:
    """
    The Plan Gate's answer. `standing` is approval that also covers
    revisions of this Plan. Measured: 69 of 135 minutes spent waiting at
    a Gate, against 6s of FreeCAD, and it does not cover an invented
    value the user has not read -- an undimensioned cutout once changed
    from rectangle to R15 semicircle to R30 circle under a standing
    approval.
    """

    decision: str = APPROVE
    feedback: str = ""
    standing: bool = False
    steps: tuple = ()


def auto_approve(steps, history=(), rollback=None):
    """
    The gate when nothing is wired up to ask.

    Level 1, Level 2 and headless runs use this. The panel passes a real gate,
    and a real gate is the only reason a Step ever runs in front of a user.
    """
    return Approval(APPROVE)


ACCEPT = "accept"


REVISE = "revise"


START_OVER = "start_over"


DECISIONS = (ACCEPT, REVISE, START_OVER)


def revise_by_default(failure):
    """
    The decider when nothing is wired up to ask.

    Level 1, Level 2 and headless runs use this, and it is the behaviour the
    loop had before there was a question: no automatic acceptance of a failed
    result, and no automatic licence to wipe the model.
    """
    return REVISE


DECIDE_FOR_ME = ""


_DECIDE = ("Nobody is asked for a value, and Design could not settle it either -- it stays "
           "`not_defined`. Do not invent a number: plan around it, or say so in `question` "
           "if the part cannot be built without it.")


_ABOUT_THE_BUILD = ("material", "materials", "uuid", "uuids", "card", "cards", "density",
                    "type", "types", "property", "properties", "enumeration", "spelling",
                    "spellings", "installed")


def _a_question_for_the_build(question):
    """
    Whether a question is about this FreeCAD rather than about the part.
    A dimension, or which of two profiles governs, is a question the
    drawing can settle. A material card's uuid, a type's name, a
    property's spelling is not — no sheet prints one, and a recorded run
    spent every re-read asking the drawing for a material uuid instead.
    """
    words = re.findall(r"[a-z]+", str(question).lower())
    return any(word in _ABOUT_THE_BUILD for word in words)


def nobody_to_ask(question):
    """
    The answerer when nothing is wired up to ask a person.

    Level 1, Level 2 and headless runs use this: it declines, which sends Plan
    down the decide-for-yourself-and-say-so path rather than stalling a run
    nobody is watching.
    """
    return DECIDE_FOR_ME


class NeedsAnswer(Exception):
    """
    Raised by an `ask` callback that has nobody to ask.

    `copilot.sdk.Client` raises this instead of guessing when its caller
    supplied no answer callback; `run` turns it into a blocked, resumable
    `LoopResult` rather than a failure, carrying the question itself as the
    reason so the caller can answer it later and pick the same run back up.
    """
