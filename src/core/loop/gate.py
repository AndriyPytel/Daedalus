"""The Plan Gate: approval, revision decisions, and user questions."""
import re
from dataclasses import dataclass, field



APPROVE = "approve"


REJECT = "reject"


AMEND = "amend"


@dataclass
class Approval:
    """
    The Plan Gate's answer.

    Approval covers every Step in the Plan; there is no per-Step approval.

    `standing` is approval that also covers the Plans this one turns into. A
    Plan is revised for six reasons and five of them are the run correcting
    itself — a Step Execute could not build, checks that did not hold, a part
    of the description nothing planned — and each revision stopped at the Gate
    for an approval the user had, in substance, already given. In a recorded
    session that came to sixty-nine minutes of a hundred and thirty-five spent
    waiting at a Gate, against six seconds of FreeCAD. A standing
    approval says "get on with it": the run stops asking about revisions of
    this Plan, and every other question it can put to the user — the user's own
    objects, a rebuild of the model, what to do about a result that did not
    pass — is untouched, because those are the ones that are not about the Plan.

    What it does not cover is a value nobody supplied that the user has not
    read. A Step states what it assumed precisely so a wrong guess is
    correctable at the Gate, and the guess is made again from nothing every
    time the Plan is revised. In a recorded session
    (`logs/sessions/141a41df15544013a9740a45138fa7ad.jsonl`, the 2023 pipe
    clamp) an undimensioned cutout was approved as a rectangle, came back from
    the first revision as a semicircular R15 and from the second as a circular
    R30 that swallowed the ring the clamp exists to close — and the user saw
    only the rectangle, because the approval stood. So a revision carrying an
    assumption they have not read reaches the Gate however standing the
    approval is: what stands is approval of the path, and an invented value is
    not the path.

    `steps` is the Plan the Gate is actually approving, if it differs from
    the one it was shown: the panel's Plan Gate is edited directly now, not
    amended through the model, and Build hands back whatever it
    holds at the moment it is pressed. Empty means unedited -- run continues
    with the Steps `plan()` produced.
    """

    decision: str = APPROVE
    feedback: str = ""
    standing: bool = False
    steps: tuple = ()


def auto_approve(steps):
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


_DECIDE = ("Nobody is asked for a value. Choose sensible values yourself, and write every "
           "value you chose into the prose of the Step that uses it, so a wrong choice is "
           "correctable at the Plan Gate.")


_ABOUT_THE_BUILD = ("material", "materials", "uuid", "uuids", "card", "cards", "density",
                    "type", "types", "property", "properties", "enumeration", "spelling",
                    "spellings", "installed")


def _a_question_for_the_build(question):
    """
    Whether a question is about this FreeCAD rather than about the part.

    A dimension, or which of two profiles governs, is a question the drawing
    can settle. A material card's uuid, a type's name, a property's spelling is
    not: no sheet prints one, and a recorded run spent every re-read it had
    putting "the UUID of an installed plain carbon steel material card" to the
    drawing before ending for want of an answer.
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
