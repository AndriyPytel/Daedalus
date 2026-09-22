"""Findings about a `from` field that cites a numeral or mark absent from
the sheet's own text layer -- prose describing where a number came from is
never checked, only the numerals and marks quoted inside it, since checking
prose would refuse every citation whose surrounding words are the model's
own description rather than a quotation.
"""


import re

from core.design.core import _conflict, _filled
from core.design.gaps import _each_value


_MARK = re.compile(r"\(?\b[A-Z]?\d+(?:\.\d+)?[A-Z]?\)?")


def _marks(citation):
    return _MARK.findall(citation)


def fabricated_citation(model, sheet_words) -> list:
    """
    Every `from` field quoting a numeral or mark that the sheet's text
    layer does not carry. The reading itself is left on the model --
    refused values travel on in `conflicts`, the way `_refused_conflicts`
    (`core.design.merge`) leaves a bad conflict entry in place rather than
    dropping it.

    A mark is compared to a whole sheet word, never a substring of one:
    `from` citing bare `8` is not satisfied by the sheet printing `R8`,
    and citing bare `4` is not satisfied by `4X` -- the letter is part of
    the mark, not decoration around a number, so a citation must quote it
    to match.
    """
    words = set(sheet_words or ())
    found = []
    for path, node in _each_value(model, ""):
        citation = node.get("from")
        if not _filled(citation):
            continue
        missing = [mark for mark in _marks(citation) if mark not in words]
        if missing:
            found.append(_conflict(
                path,
                "`from` cites %s, not on the sheet's text layer"
                % ", ".join(missing),
                citation))
    return found
