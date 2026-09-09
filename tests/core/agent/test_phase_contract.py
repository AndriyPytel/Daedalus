"""
FCC-305: the declared phase-visibility table, and its spawn function.
"""
import os
import stat

import pytest

from core.agent import phase_contract as pc


def test_table_covers_every_phase():
    for phase in (pc.DESIGN, pc.PLAN, pc.EXECUTE, pc.ASSERTION, pc.REVIEW):
        assert phase in pc.PHASE_CONTRACT


def test_plan_sees_only_design():
    assert set(pc.PHASE_CONTRACT[pc.PLAN]) == {"design"}


def test_execute_sees_plan_and_design():
    seen = set(pc.PHASE_CONTRACT[pc.EXECUTE])
    assert seen >= {"plan", "design"}
    assert "execute" not in seen or pc.PHASE_CONTRACT[pc.EXECUTE].get("execute") != "ro"


def test_assertion_sees_design_and_a_readonly_document_but_no_execute():
    seen = pc.PHASE_CONTRACT[pc.ASSERTION]
    assert set(seen) == {"design", "document"}
    assert seen["document"] == pc.RO


def test_review_sees_no_code_at_all():
    seen = pc.PHASE_CONTRACT[pc.REVIEW]
    assert set(seen) == {"intent", "outcome"}
    assert "design" not in seen and "plan" not in seen and "execute" not in seen


SOURCES = {
    "intent": "make a bracket",
    "design": {"kind": "bracket"},
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


def test_spawn_assertion_directory_has_design_and_readonly_document_no_execute(tmp_path):
    working = pc.spawn(pc.ASSERTION, str(tmp_path / "assertion"), **_sources(tmp_path))
    names = set(os.listdir(working))
    assert names == {"design.json", "document.FCStd"}
    mode = os.stat(os.path.join(working, "document.FCStd")).st_mode
    assert not (mode & stat.S_IWUSR)


def test_spawn_review_directory_has_no_code_at_all(tmp_path):
    working = pc.spawn(pc.REVIEW, str(tmp_path / "review"), **_sources(tmp_path))
    names = set(os.listdir(working))
    assert names == {"intent.json", "outcome.json"}


def test_spawn_ignores_a_source_the_table_does_not_declare_for_that_phase(tmp_path):
    working = pc.spawn(pc.PLAN, str(tmp_path / "plan"), **_sources(tmp_path))
    assert set(os.listdir(working)) == {"design.json"}


def test_spawn_rejects_an_unknown_phase(tmp_path):
    with pytest.raises(KeyError):
        pc.spawn("nonsense", str(tmp_path / "x"), **_sources(tmp_path))
