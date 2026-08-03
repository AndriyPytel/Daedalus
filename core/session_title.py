"""
The one short call that turns a Session's first Intent into its name.

Separate from the run on purpose: `plan()` runs again on every revision
(`core/loop.py`), so a title asked for alongside the Steps would be regenerated
mid-run or need bookkeeping of its own (FCC-44).

Importable without FreeCAD — the settings lookup is deferred into `generate`.
"""
from core.llm import TEXT

LIMIT = 40

# A title long enough to be a sentence is a model that ignored the instruction,
# and a sentence in the picker is the thing this ticket exists to remove.
CEILING = 60

PROMPT = (
    "Name this CAD modelling session in at most six words, as a noun phrase "
    "describing the part being made. Reply with the title alone — no quotes, "
    "no trailing period, no preamble."
)


def fallback(intent: str) -> str:
    """What a Session is called until a generated title lands, or if none does."""
    intent = " ".join((intent or "").split())
    if not intent:
        return "Untitled Session"
    if len(intent) <= LIMIT:
        return intent
    return intent[:LIMIT].rstrip() + "…"


def generate(intent: str, model_id: str = None, client=None) -> str:
    """The generated title, or "" when the provider gave nothing usable."""
    if client is None:
        from core.llm import client_from_settings

        client = client_from_settings(model_id)
    try:
        said = "".join(chunk.text for chunk in client.stream(
            [{"role": "system", "content": PROMPT},
             {"role": "user", "content": intent}])
            if chunk.kind == TEXT)
    except Exception:
        return ""
    return _clean(said)


def _clean(said: str) -> str:
    title = said.strip().splitlines()[0].strip() if said.strip() else ""
    title = title.strip("\"'").rstrip(".").strip()
    return title if 0 < len(title) <= CEILING else ""
