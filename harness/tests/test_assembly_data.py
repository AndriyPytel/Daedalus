import json
from pathlib import Path

import pytest

import assembly_score
import assembly_tasks

ROOT = assembly_tasks.OUT
pytestmark = pytest.mark.skipif(not (ROOT / "split.json").exists(), reason="test-data/assembly not built")


def tasks(mode):
    return sorted(p for p in (ROOT / mode).iterdir() if p.is_dir())


@pytest.fixture(scope="module")
def splits():
    return json.loads((ROOT / "split.json").read_text())


@pytest.mark.parametrize("mode", assembly_tasks.MODES)
def test_each_mode_has_the_configured_task_count_and_split(mode, splits):
    names = [p.name for p in tasks(mode)]
    assert len(names) == assembly_tasks.TASKS_PER_MODE
    count = assembly_tasks.make_sheets.test_count(assembly_tasks.TASKS_PER_MODE)
    assert (len(splits[mode]["test"]), len(splits[mode]["validation"])) == (count, assembly_tasks.TASKS_PER_MODE - count)
    assert sorted(splits[mode]["test"] + splits[mode]["validation"]) == names


@pytest.mark.parametrize("mode", assembly_tasks.MODES)
def test_every_task_names_its_source_and_licence_in_credits(mode):
    credits = (ROOT / "CREDITS.md").read_text()
    for task in tasks(mode):
        meta = json.loads((task / "task.json").read_text())
        assert meta["mode"] == mode
        assert meta["licence"] and meta["source"]
        assert task.name in credits and meta["licence"] in credits
        assert (task / "reference.FCStd").exists()


def test_from_scratch_tasks_carry_a_graph_and_no_part_files():
    for task in tasks("from_scratch"):
        meta = json.loads((task / "task.json").read_text())
        assert meta["inputs"]["graph"]["nodes"] and meta["inputs"]["graph"]["edges"]
        assert not (task / "parts").exists()


def test_edit_tasks_carry_input_and_expected_assemblies():
    for task in tasks("edit"):
        assert (task / "input.FCStd").exists() and (task / "reference.FCStd").exists()
        assert json.loads((task / "task.json").read_text())["text"]


def test_every_reference_solves_and_scores_one_against_itself():
    references = [t / "reference.FCStd" for mode in assembly_tasks.MODES for t in tasks(mode)]
    for path, measured in zip(references, assembly_score.measure(references)):
        assert assembly_score.combine(measured, measured)["score"] == 1.0, path
        assert measured["solve"] == 0, path


def test_credits_record_the_licences_of_sources_checked():
    credits = (ROOT / "CREDITS.md").read_text()
    for source in ("MUSE", "IndustrialCAD-1400", "MSD"):
        assert source in credits


@pytest.mark.parametrize("mode", assembly_tasks.MODES)
def test_task_directories_are_self_contained(mode):
    for task in tasks(mode):
        meta = json.loads((task / "task.json").read_text())
        assert ".cache" not in json.dumps(meta) and str(ROOT.parents[1]) not in json.dumps(meta)
        for part in meta["reference_spec"]["parts"]:
            assert not Path(part["step"]).is_absolute()
            assert (task / part["step"]).exists()
        for part in meta["inputs"].get("parts", []):
            assert (task / part["file"]).exists()


def test_every_edit_task_input_and_reference_pass_the_assertion():
    paths = [str(t / name) for t in tasks("edit") for name in ("input.FCStd", "reference.FCStd")]
    assert assembly_tasks.assertion(paths) == {path: None for path in paths}


def test_the_edit_test_split_has_at_least_four_retypes():
    split = json.loads((ROOT / "split.json").read_text())["edit"]["test"]
    kinds = [json.loads((ROOT / "edit" / name / "task.json").read_text())["inputs"]["edit"] for name in split]
    assert kinds.count("retype") >= 4

