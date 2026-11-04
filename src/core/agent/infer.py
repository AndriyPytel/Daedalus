"""
The Domain a run builds under: the Document's own Assembly decides first, one short model call decides otherwise, and the user's pick skips both.
The call carries the Intent and a summary of the Document's object types, takes no tools and is asked at the lowest effort; an answer that is not "assembly" is Part.
"""
import collections
import copy

from core.agent.domain import ASSEMBLY, PART
import time

from core.events import COST, DOMAIN
from core.llm import EFFORTS, TEXT
from core.settings import AUTO

CHOICES = (AUTO, PART, ASSEMBLY)

PROMPT = (
    "Decide which kind of CAD job this request is. Answer with one word: "
    "'assembly' if it joins, mates, places or edits several parts together, "
    "'part' if it models, or edits, a single part."
)


def from_document(types):
    """`ASSEMBLY` where *types* holds an Assembly object, else `None`."""
    return ASSEMBLY if any(t.startswith("Assembly::") for t in types) else None


def summary(types):
    """The object types of a Document, counted, in the order first met."""
    counts = collections.Counter(types)
    return ", ".join("%s x%d" % (kind, n) for kind, n in counts.items()) or "empty"


def classify(intent, types, llm, events=None):
    """The Domain *llm* names for *intent* and a Document of *types*, its cost said on *events*; `PART` where the answer cannot be read."""
    client = copy.copy(llm)
    client.effort = EFFORTS[0]
    start = time.time()
    said = "".join(chunk.text for chunk in client.stream(
        [{"role": "system", "content": PROMPT},
         {"role": "user", "content": "Request: %s\n\nThe Document holds: %s" % (intent, summary(types))}])
        if chunk.kind == TEXT)
    if events is not None:
        spent = getattr(client, "last_cost", None) or {}
        events.emit(COST, phase="domain", seconds=time.time() - start,
                    model=spent.get("model", getattr(client, "model", "")),
                    **{k: v for k, v in spent.items() if k != "model"})
    return ASSEMBLY if said.strip().strip(".!\"'").lower() == ASSEMBLY else PART


def choose(choice, intent, types, llm, events):
    """The Domain for *choice*: itself where it names one, else inferred from the Document and then the model; it is said on *events* before the loop starts."""
    inferred = choice == AUTO
    domain = (from_document(types) or classify(intent, types, llm, events)) if inferred else choice
    events.emit(DOMAIN, domain=domain, inferred=inferred)
    return domain
