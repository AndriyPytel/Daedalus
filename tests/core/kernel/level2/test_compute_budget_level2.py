"""
Level 2 -- what the kernel costs on part 202, the worst case the profile measured (2157 faces, 1.0M triangles).

Each threshold is asserted as agreed; a margin that proves flaky is a finding, not a number to loosen.
The reference render is what the per-triangle rasteriser drew of the fixture, source left out.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

import numpy as np
import pytest
from PIL import Image

from core.kernel import Kernel, find_freecadcmd
from core.kernel import kernel as kernel_module
from core.kernel.preflight_render import render
from core.edit_guard import RememberedMeasures
from tests.core.kernel import part_202

pytestmark = pytest.mark.freecad

RENDER_SECONDS = 3.0
FIXED_CHECKS_SECONDS = 8.0
PROBE_SECONDS = 1.0
REFERENCE = os.path.join(os.path.dirname(part_202.FIXTURE), "part_202_reference.png")
SRC = os.path.dirname(os.path.dirname(os.path.dirname(kernel_module.__file__)))


@pytest.fixture(scope="module")
def doc():
    import FreeCAD

    document = part_202.opened()
    try:
        yield document
    finally:
        FreeCAD.closeDocument(document.Name)


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


@pytest.fixture
def kernel(scratch):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _leftovers(directory):
    return sorted(os.path.join(folder, name) for folder, folders, files in os.walk(directory)
                  for name in folders + files if "kernel_checkpoint_" in os.path.join(folder, name))


def _a_parent_with_a_kernel(scratch, landing):
    """A separate process that starts a Kernel, optionally lands a Step, and says `up`; returns it with the worker's pid."""
    code = ("import sys, time\nsys.path.insert(0, %r)\nfrom core.kernel import Kernel\nk = Kernel()\n"
            "k.start()\n%sprint('up', k.pid, flush=True)\ntime.sleep(60)\n" % (
                SRC, "assert k.check(\"doc.addObject('Part::Box', 'Box')\", document=None).ok\n" if landing else ""))
    parent = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                              env=dict(os.environ, TMPDIR=str(scratch)))
    said = parent.stdout.readline().split()
    assert said[0] == "up"
    return parent, int(said[1])


def _until_gone(directory):
    deadline = time.monotonic() + 15
    while _leftovers(directory) and time.monotonic() < deadline:
        time.sleep(0.2)


def test_the_render_of_part_202_is_the_picture_the_per_triangle_rasteriser_drew(doc, tmp_path):
    path = render(doc, str(tmp_path / "part_202.png"))

    drawn = np.array(Image.open(path).convert("RGB"))
    reference = np.array(Image.open(REFERENCE).convert("RGB"))

    differing = int((drawn != reference).any(axis=2).sum())
    assert differing == 0, "%d of %d pixels differ from the reference render" % (differing, reference.shape[0] * reference.shape[1])


def test_the_exact_box_found_from_few_faces_is_the_exact_box_and_is_found_faster(doc):
    shape = doc.Part__Feature.Shape

    started = time.perf_counter()
    whole = shape.optimalBoundingBox(False)
    whole_took = time.perf_counter() - started
    started = time.perf_counter()
    found = RememberedMeasures().box(shape)
    found_took = time.perf_counter() - started

    for side in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"):
        assert getattr(found, side) == pytest.approx(getattr(whole, side), abs=1e-6)
    assert found_took < whole_took, "%.2f s from the faces against %.2f s for the whole shape" % (found_took, whole_took)


def test_a_read_only_probe_writes_no_checkpoint_and_no_snapshot_and_costs_a_second_at_most(kernel, scratch):
    landed = kernel.check("pass", document=part_202.saved_whole(str(scratch)), timeout=300)
    assert landed.ok, landed.traceback
    written = os.stat(landed.checkpoint).st_mtime_ns
    files = _leftovers(scratch)

    started = time.perf_counter()
    probe = kernel.check("print(len(doc.Objects))")
    took = time.perf_counter() - started

    assert probe.ok, probe.traceback
    assert os.stat(landed.checkpoint).st_mtime_ns == written, "the probe rewrote the checkpoint"
    assert _leftovers(scratch) == files, "the probe left a file behind"
    assert probe.geometry == {}, "the probe took a geometry snapshot"
    assert probe.signature == {}, "the probe reduced the Document to a signature"
    assert took <= PROBE_SECONDS, "a read-only probe on part 202 took %.2f s, the threshold is %.1f s" % (
        took, PROBE_SECONDS)


def test_a_step_that_writes_rewrites_the_checkpoint_and_returns_the_snapshot(kernel):
    first = kernel.check("doc.addObject('Part::Box', 'Box')", document=None)
    assert first.ok, first.traceback
    written = os.stat(first.checkpoint).st_mtime_ns

    second = kernel.check("doc.Box.Length = 7")

    assert second.ok, second.traceback
    assert os.stat(second.checkpoint).st_mtime_ns != written, "the writing Step left the checkpoint as it was"
    assert set(second.geometry) == {"Box"}


def test_a_kernel_that_is_killed_normally_leaves_no_kernel_checkpoint_behind(kernel, scratch):
    assert kernel.check("doc.addObject('Part::Box', 'Box')", document=None).ok
    assert kernel.check("doc.Box.Length = 7").ok
    assert _leftovers(scratch), "the run wrote no checkpoint, so there is nothing to clean"

    kernel.kill()

    assert _leftovers(scratch) == []


def test_a_run_whose_kernel_was_sigkilled_leaves_no_kernel_checkpoint_behind(kernel, scratch):
    assert kernel.check("doc.addObject('Part::Box', 'Box')", document=None).ok
    os.kill(kernel.pid, signal.SIGKILL)
    kernel.check("pass")
    assert kernel.check("doc.Box.Length = 7").ok
    os.kill(kernel.pid, signal.SIGKILL)

    kernel.kill()

    assert _leftovers(scratch) == []


@pytest.mark.parametrize("landing", [True, False], ids=["after a checkpoint", "before the first checkpoint"])
def test_a_kernel_whose_parent_was_killed_removes_its_scratch_directory(scratch, landing):
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    parent, _ = _a_parent_with_a_kernel(scratch, landing)
    try:
        assert _leftovers(scratch), "the kernel has no scratch directory yet"
        parent.kill()
        parent.wait()
        _until_gone(scratch)
    finally:
        parent.kill()

    assert _leftovers(scratch) == []
