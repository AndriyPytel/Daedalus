
import json

import pytest

import bim_score
import make_sheets
import bim_tasks

ROOT = bim_tasks.OUT
pytestmark = pytest.mark.skipif(not (ROOT / "split.json").exists(), reason="test-data/bim not built")


def tasks(mode):
    return sorted(p for p in (ROOT / mode).iterdir() if p.is_dir())


@pytest.fixture(scope="module")
def splits():
    return json.loads((ROOT / "split.json").read_text())


@pytest.mark.parametrize("mode", bim_tasks.MODES)
def test_each_mode_has_the_configured_task_count_and_a_seeded_split(mode, splits):
    names = [p.name for p in tasks(mode)]
    assert len(names) == bim_tasks.TASKS_PER_MODE
    tests = make_sheets.test_count(bim_tasks.TASKS_PER_MODE)
    assert (len(splits[mode]["test"]), len(splits[mode]["validation"])) == (tests, bim_tasks.TASKS_PER_MODE - tests)
    assert sorted(splits[mode]["test"] + splits[mode]["validation"]) == names
    assert splits["seed"] == bim_tasks.SEED


@pytest.mark.parametrize("mode", bim_tasks.MODES)
def test_every_task_is_self_contained_and_credited(mode):
    credits = (ROOT / "CREDITS.md").read_text()
    for task in tasks(mode):
        meta = json.loads((task / "task.json").read_text())
        assert meta["mode"] == mode and meta["licence"] and meta["source"] == "MSD"
        assert task.name in credits and meta["licence"] in credits
        assert (task / "reference.FCStd").exists() and (task / "reference.ifc").exists()
        assert str(ROOT.parents[1]) not in json.dumps(meta)


def test_create_tasks_carry_a_floor_plan_image_and_a_short_text():
    for task in tasks("create"):
        meta = json.loads((task / "task.json").read_text())
        assert (task / meta["inputs"]["plan"]).exists() and meta["text"]


def test_edit_tasks_carry_the_input_model_one_edit_text_and_the_edit_kind():
    kinds = set()
    for task in tasks("edit"):
        meta = json.loads((task / "task.json").read_text())
        assert (task / "input.FCStd").exists() and (task / "input.ifc").exists() and meta["text"]
        assert meta["inputs"]["edit"] in bim_tasks.EDIT_FUNCTIONS
        kinds.add(meta["inputs"]["edit"])
    assert kinds == set(bim_tasks.EDIT_FUNCTIONS)


def test_credits_record_the_licence_findings():
    credits = (ROOT / "CREDITS.md").read_text()
    for source in ("MSD", "ResPlan", "BIM-Edit", "CC BY-SA 4.0"):
        assert source in credits


def test_every_reference_scores_one_against_itself():
    paths = [t / "reference.ifc" for mode in bim_tasks.MODES for t in tasks(mode)]
    for path, measured in zip(paths, bim_score.measure(paths)):
        assert measured["opens"] and measured["counts"]["IfcSpace"] >= 2, path
        assert bim_score.combine(measured, measured)["score"] == 1.0, path


def test_every_edit_changes_the_model_the_scorer_sees():
    for task in tasks("edit"):
        before, after = bim_score.measure([task / "input.ifc", task / "reference.ifc"])
        assert bim_score.combine(before, after)["score"] < 1.0, task


@pytest.mark.parametrize("mode", bim_tasks.MODES)
def test_every_reference_holds_exactly_the_elements_of_its_spec(mode):
    keys = (("IfcWall", "walls"), ("IfcDoor", "doors"), ("IfcWindow", "windows"), ("IfcSpace", "spaces"))
    paths = [t / "reference.ifc" for t in tasks(mode)]
    for task, measured in zip(tasks(mode), bim_score.measure(paths)):
        spec = json.loads((task / "task.json").read_text())["reference_spec"]
        assert {t: measured["counts"][t] for t, _ in keys} == {t: len(spec[k]) for t, k in keys}, task
        assert all(o["host"] is not None for o in measured["openings"]), task


def test_edit_scores_zero_for_the_unedited_input_and_one_for_the_reference():
    for task in tasks("edit"):
        original, expected = bim_score.measure([task / "input.ifc", task / "reference.ifc"])
        assert bim_score.edit_combine(original, expected, original)["score"] == 0.0, task
        assert bim_score.edit_combine(expected, expected, original)["score"] == 1.0, task
