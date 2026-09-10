"""The live Document, copied for a phase kernel to open."""
import contextlib

from core.freecad_apply import _discard


@contextlib.contextmanager
def copied(tools):
    """
    The path a phase's kernel opens, deleted when the phase is done with it.

    Which phases get it is `PHASE_CONTRACT`'s to say, not this helper's:
    Execute builds on the Document and Assertion measures it, and a phase
    whose row omits it is handed nothing on disk however this is called.
    """
    path = tools.call("document_copy")["path"]
    try:
        yield path
    finally:
        if path:
            _discard(path)
