"""
The declared phase-visibility table, and its spawn function.
"""
import os
import stat

import pytest

from core.agent import phase_contract as pc


def test_plan_writes_nothing_to_disk():
    assert pc.PHASE_CONTRACT[pc.PLAN] == {}


def test_execute_sees_plan_and_a_writable_document():
    seen = pc.PHASE_CONTRACT[pc.EXECUTE]
    assert seen == {"plan": pc.RO, "document": pc.RW}


SOURCES = {
    "intent": "make a bracket",
    "plan": [{"do": "pad 10mm"}],
    "execute": {"trace": "ok"},
    "document": None,  # filled in per-test with a real path
    "outcome": {"verdict": "pass"},
}


def _sources(tmp_path):
    doc = tmp_path / "doc.FCStd"
    doc.write_bytes(b"not a real document, just bytes to copy")
    sources = dict(SOURCES)
    sources["document"] = str(doc)
    return sources


def test_spawn_assertion_directory_has_only_a_readonly_document(tmp_path):
    working = pc.spawn(pc.ASSERTION, str(tmp_path / "assertion"), **_sources(tmp_path))
    names = set(os.listdir(working))
    assert names == {"document.FCStd"}
    mode = os.stat(os.path.join(working, "document.FCStd")).st_mode
    assert not (mode & stat.S_IWUSR)


def test_spawn_ignores_a_source_the_table_does_not_declare_for_that_phase(tmp_path):
    working = pc.spawn(pc.PLAN, str(tmp_path / "plan"), **_sources(tmp_path))
    assert set(os.listdir(working)) == set()


def test_spawn_rejects_an_unknown_phase(tmp_path):
    with pytest.raises(KeyError):
        pc.spawn("nonsense", str(tmp_path / "x"), **_sources(tmp_path))
