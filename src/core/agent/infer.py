"""
The Domain a run builds under: the Document's own Assembly or Arch objects decide first, one short model call decides otherwise, and the user's pick skips both.
The call carries the Intent and a summary of the Document's object types, takes no tools and is asked with the run's own model and Effort; an answer that is neither "assembly" nor "bim" is Part.
"""
import collections

from core.agent.domain import ASSEMBLY, BIM, PART
import time

from core.events import COST, DOMAIN
from core.llm import TEXT
from core.prompts import read_prompt
from core.settings import AUTO

CHOICES = (AUTO, PART, ASSEMBLY, BIM)


def from_document(types, arch=False):
    """`ASSEMBLY` where *types* holds an Assembly object, else `BIM` where *arch* says the Document holds an Arch object, else `None`."""
    if any(t.startswith("Assembly::") for t in types):
        return ASSEMBLY
    return BIM if arch else None


def summary(types):
    """The object types of a Document, counted, in the order first met."""
    counts = collections.Counter(types)
    return ", ".join("%s x%d" % (kind, n) for kind, n in counts.items()) or "empty"


def classify(intent, types, llm, events=None):
    """The Domain *llm* names for *intent* and a Document of *types*, its cost said on *events*; `PART` where the answer cannot be read."""
    start = time.time()
    said = "".join(chunk.text for chunk in llm.stream(
        [{"role": "system", "content": read_prompt("domain.md").strip()},
         {"role": "user", "content": "Request: %s\n\nThe Document holds: %s" % (intent, summary(types))}])
        if chunk.kind == TEXT)
    if events is not None:
        spent = getattr(llm, "last_cost", None) or {}
        events.emit(COST, phase="domain", seconds=time.time() - start,
                    model=spent.get("model", getattr(llm, "model", "")),
                    **{k: v for k, v in spent.items() if k != "model"})
    answer = said.strip().strip(".!\"'").lower()
    return answer if answer in (ASSEMBLY, BIM) else PART


def choose(choice, intent, types, llm, events, arch=False):
    """The Domain for *choice*: itself where it names one, else inferred from the Document (*types*, and *arch* for an Arch object in it) and then the model; it is said on *events* before the loop starts."""
    inferred = choice == AUTO
    domain = (from_document(types, arch) or classify(intent, types, llm, events)) if inferred else choice
    events.emit(DOMAIN, domain=domain, inferred=inferred)
    return domain
