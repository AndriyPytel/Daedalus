"""
Offline shape assertions for the CADGenBench submission bundle: the zip
layout and `meta.json` contract this repo's `cadgenbench_bundle` mirrors
from `docs/benchmark/submission.md` and `cadgenbench.baseline.package`
(Apache-2.0), never a network call to HuggingFace.
"""
import json
import os
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import cadgenbench_bundle as bundle  # noqa: E402


def _write_output(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"ISO-10303-21;")


def test_plan_reports_found_and_missing_per_fixture(tmp_path):
    run_dir = tmp_path / "run"
    _write_output(str(run_dir / "101" / "output.step"))
    (run_dir / "102").mkdir(parents=True)

    plan = bundle.plan_bundle(str(run_dir), ["101", "102", "103"])

    assert [entry["fixture"] for entry in plan] == ["101", "102", "103"]
    assert plan[0]["status"] == "found"
    assert plan[0]["candidate"].endswith("101/output.step")
    assert plan[1]["status"] == "missing"
    assert plan[1]["candidate"] is None
    assert plan[2]["status"] == "missing"


def test_plan_accepts_output_stp_as_well_as_output_step(tmp_path):
    run_dir = tmp_path / "run"
    _write_output(str(run_dir / "101" / "output.stp"))

    plan = bundle.plan_bundle(str(run_dir), ["101"])

    assert plan[0]["status"] == "found"
    assert plan[0]["candidate"].endswith("101/output.stp")


def test_bundle_writes_one_folder_per_expected_fixture_even_when_missing(tmp_path):
    run_dir = tmp_path / "run"
    _write_output(str(run_dir / "101" / "output.step"))
    out_path = tmp_path / "submission.zip"

    report = bundle.write_bundle(str(run_dir), ["101", "102"], str(out_path),
                                 submitter="dev", submission_name="test run")

    assert report["n_found"] == 1
    assert report["n_missing"] == 1
    assert report["missing"] == ["102"]

    with zipfile.ZipFile(str(out_path)) as zf:
        names = set(zf.namelist())
        assert "101/" in names
        assert "101/output.step" in names
        assert "102/" in names
        assert "102/output.step" not in names
        assert "102/output.stp" not in names

        meta = json.loads(zf.read("meta.json"))
        assert meta["submitter_name"] == "dev"
        assert meta["submission_name"] == "test run"
        assert meta["agree_to_publish"] is False


def test_bundle_never_agrees_to_publish_unless_asked_explicitly(tmp_path):
    run_dir = tmp_path / "run"
    _write_output(str(run_dir / "101" / "output.step"))
    out_path = tmp_path / "submission.zip"

    bundle.write_bundle(str(run_dir), ["101"], str(out_path), agree=True)

    with zipfile.ZipFile(str(out_path)) as zf:
        meta = json.loads(zf.read("meta.json"))
    assert meta["agree_to_publish"] is True


def test_generation_fixtures_reads_the_cached_manifest():
    fixtures = bundle.generation_fixtures()

    assert len(fixtures) == 49
    assert "101" in fixtures


def test_record_milestone_always_carries_the_incomparability_note(tmp_path):
    path = tmp_path / "milestones.jsonl"

    bundle.record_milestone(str(path), milestone="m1", local_score=0.71,
                             leaderboard_score=0.42)

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["milestone"] == "m1"
    assert record["local_score"] == 0.71
    assert record["leaderboard_score"] == 0.42
    assert "never compared" in record["note"].lower()


def test_record_milestone_appends_one_line_per_call(tmp_path):
    path = tmp_path / "milestones.jsonl"

    bundle.record_milestone(str(path), milestone="m1", local_score=0.71,
                             leaderboard_score=0.42)
    bundle.record_milestone(str(path), milestone="m2", local_score=0.80,
                             leaderboard_score=0.55)

    assert len(path.read_text().splitlines()) == 2


def test_bundle_writes_an_empty_folder_for_every_fixture_it_does_not_attempt(tmp_path):
    run_dir = tmp_path / "run"
    _write_output(str(run_dir / "101" / "output.step"))
    out_path = tmp_path / "submission.zip"

    report = bundle.write_bundle(str(run_dir), ["101"], str(out_path), empty=["201", "202"])

    assert report["missing"] == []
    with zipfile.ZipFile(str(out_path)) as zf:
        names = set(zf.namelist())
    assert {"201/", "202/"} <= names
    assert not any(name.startswith("201/") and name != "201/" for name in names)


def test_editing_fixtures_complete_the_81_the_space_checks():
    generation, editing = bundle.generation_fixtures(), bundle.editing_fixtures()

    assert len(editing) == 32
    assert len(set(generation) | set(editing)) == 81


def test_plan_finds_the_latest_step_a_lane_run_saved_for_a_fixture(tmp_path):
    run_dir = tmp_path / "opus"
    _write_output(str(run_dir / "101.20260930-193313-925084.step"))
    _write_output(str(run_dir / "101.20260930-201415-846555.step"))
    _write_output(str(run_dir / "1010.20260930-201415-846555.step"))

    plan = bundle.plan_bundle(str(run_dir), ["101", "102"])

    assert plan[0]["candidate"].endswith("101.20260930-201415-846555.step")
    assert plan[1]["status"] == "missing"

    out_path = tmp_path / "submission.zip"
    bundle.write_bundle(str(run_dir), ["101"], str(out_path))
    with zipfile.ZipFile(str(out_path)) as zf:
        assert "101/output.step" in zf.namelist()
