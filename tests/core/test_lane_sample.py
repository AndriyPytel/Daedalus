"""
Level 1 -- `tools/lane.py` draws its tasks from `test-data/split.json` and scores them against `test-data/<split>/<label>/exemplar.step`.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402

LABELS = ["a_000", "b_001", "c_002", "d_003", "e_004", "f_005"]


def _test_data(tmp_path, monkeypatch):
    for label in LABELS:
        folder = tmp_path / "validation" / label
        folder.mkdir(parents=True)
        (folder / "exemplar.step").write_text("exemplar")
        (folder / "sheet.pdf").write_text("sheet")
    (tmp_path / "split.json").write_text(json.dumps({"validation": LABELS, "test": ["t_000"]}))
    monkeypatch.setenv("DAE_TEST_DATA_DIR", str(tmp_path))
    return tmp_path


def test_exemplar_for_finds_a_test_data_label(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _test_data(data, monkeypatch)
    monkeypatch.setenv("DAE_MODELMANIA_DIR", str(tmp_path / "none"))

    assert lane._exemplar_for("c_002") == str(data / "validation" / "c_002" / "exemplar.step")
    assert lane._exemplar_for("c_002-rep1") == str(data / "validation" / "c_002" / "exemplar.step")
    assert lane._exemplar_for("nowhere") is None


def test_sample_draws_n_distinct_labels_of_the_split_and_writes_them_to_the_out_dir(
        monkeypatch, tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    _test_data(data, monkeypatch)
    out = tmp_path / "out"
    out.mkdir()

    tasks = lane.sampled_tasks("validation", 3, str(out))

    labels = [label for label, _ in tasks]
    assert len(set(labels)) == 3 and set(labels) <= set(LABELS)
    assert tasks[0][1] == str(data / "validation" / labels[0] / "sheet.pdf")
    assert (out / "labels.txt").read_text().split() == labels
    assert "seed" in capsys.readouterr().out


def test_two_samples_use_a_fresh_seed_each(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _test_data(data, monkeypatch)
    seeds = set()
    for _ in range(5):
        out = tmp_path / ("out%d" % len(seeds))
        out.mkdir()
        lane.sampled_tasks("validation", 3, str(out))
        seeds.add((out / "seed.txt").read_text())

    assert len(seeds) > 1


def test_labels_reuse_a_list_exactly(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _test_data(data, monkeypatch)
    listed = tmp_path / "labels.txt"
    listed.write_text("e_004\nb_001\n")
    out = tmp_path / "out"
    out.mkdir()

    tasks = lane.sampled_tasks("validation", None, str(out), labels_file=str(listed))

    assert [label for label, _ in tasks] == ["e_004", "b_001"]
    assert (out / "labels.txt").read_text().split() == ["e_004", "b_001"]
