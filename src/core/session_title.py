"""
The one short call that turns a Session's first Intent into its name.

Separate from the run on purpose: `plan()` runs again on every revision.
"""
from core.llm import TEXT

LIMIT = 40

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


def generate(intent: str, client=None) -> str:
    """
    The generated title, or "" when the provider gave nothing usable.

    The client is the run's own, built inside the try rather than before it: building it raises
    when the model cannot be resolved, and an exception escaping here leaves the
    Session silently unnamed.
    """
    try:
        if client is None:
            from core.llm import client_from_settings

            client = client_from_settings()
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
