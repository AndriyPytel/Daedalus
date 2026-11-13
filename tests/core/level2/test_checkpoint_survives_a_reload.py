"""
`saveCopy` is a silent no-op when the destination equals the live
Document's own `FileName` -- which is exactly what `_reload_checkpoint`
leaves it as after the first failed Step. From then on every later
`_save_checkpoint` writes nothing, and the next reload restores a Document
older than what actually landed.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_step_that_lands_between_two_failed_steps_survives_the_second_reload(kernel):
    """
    Step 1 changes the Document and fails, forcing a reload -- the
    Document's `FileName` becomes the checkpoint path from here on. Step 2
    creates X and passes. Step 3 changes the Document and fails again,
    forcing a second reload. X must still be in the kernel's Document
    afterwards: a checkpoint write that silently no-ops between the two
    reloads would lose it.
    """
    first = kernel.rehearse(
        "doc.addObject('Part::Box', 'Junk1')\n"
        "doc.recompute()\n"
        "assert False, 'boom'\n"
    )
    assert not first.ok

    second = kernel.rehearse("doc.addObject('Part::Box', 'X')")
    assert second.ok

    third = kernel.rehearse(
        "doc.addObject('Part::Box', 'Junk2')\n"
        "doc.recompute()\n"
        "assert False, 'boom again'\n"
    )
    assert not third.ok

    after = kernel.rehearse("pass")
    assert after.ok
    names = {o["name"] for o in after.objects}
    assert "X" in names, (
        "the checkpoint saved after Step 2 was a no-op -- the reload after "
        "Step 3 restored a Document from before X landed: %r" % names)
