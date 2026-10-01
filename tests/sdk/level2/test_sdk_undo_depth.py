"""Level 2 -- a fresh headless `sdk.Client` Document tracks undo across Steps."""
import pytest

from core.freecad_apply import run_in_transaction

pytestmark = pytest.mark.freecad


def _add_a_step(doc, n):
    run_in_transaction(doc, "doc.addObject('Part::Box', 'Box%d')" % n, label="Step %d" % n)


def test_a_fresh_client_document_can_undo_every_step_it_applied():
    """
    `Client(kernel=NO_KERNEL)` opens its own headless Document exactly as
    `run.py` does. Five Steps land as five transactions; the pipeline's own
    `undo_depth` must report all five undoable, not zero -- a Document whose
    `UndoMode` was never turned on keeps no undo history at all.
    """
    from sdk import Client, NO_KERNEL

    client = Client(kernel=NO_KERNEL)
    for n in range(1, 6):
        _add_a_step(client.document, n)

    assert client.pipeline.undo_depth() == 5, (
        "the live Document must record every Step's transaction on its undo stack")
