"""
The phase-visibility contract (`phase_contract`'s `PHASE_CONTRACT`), pinned as
a test rather than left a promise the model makes.

Every assertion here reads the table and `spawn()`'s own writers -- nothing
here duplicates the table's shape by hand, so a change to the table and this
test cannot silently drift apart.
"""
import os

from core.agent import phase_contract as pc


def _expected_filenames(phase):
    filenames = set()
    for artifact, mode in pc.PHASE_CONTRACT[phase].items():
        writer = pc._WRITERS[artifact]
        ext = ".FCStd" if writer is pc._write_document else ".json"
        filenames.add(artifact + ext)
    return filenames


def _sources(tmp_path):
    doc = tmp_path / "doc.FCStd"
    doc.write_bytes(b"not a real document, just bytes to copy")
    return {
        "intent": "make a bracket",
        "design": {"kind": "bracket"},
        "plan": [{"do": "pad 10mm"}],
        "execute": {"trace": "ok"},
        "document": str(doc),
        "outcome": {"verdict": "pass"},
    }


def _spawned_filenames(phase, tmp_path):
    working = pc.spawn(phase, str(tmp_path / phase), **_sources(tmp_path))
    return set(os.listdir(working))


def test_every_phases_spawned_kernel_matches_its_declared_row(tmp_path):
    for phase in pc.PHASE_CONTRACT:
        assert _spawned_filenames(phase, tmp_path) == _expected_filenames(phase)
