"""
Level 1 -- `tools/lane.py --domain bim` runs a create task of the BIM benchmark through the BIM Domain and tabulates what came of it.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402


def _task(root, label="create_01"):
    folder = root / "create" / label
    folder.mkdir(parents=True)
    (folder / "plan.png").write_bytes(b"png")
    (folder / "task.json").write_text(json.dumps({"text": "Model the flat on the plan.",
                                                  "inputs": {"plan": "plan.png"}}))
    return folder


def _command(monkeypatch, **extra):
    seen = []
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: seen.append(cmd))
    lane.run_one("t", "", "opus", "medium", 8.0, "/tmp/t.jsonl", "Do it.", "Claude (subscription)",
                 **extra)
    return seen[0]


def test_a_bim_run_names_its_domain_and_saves_an_ifc(monkeypatch):
    cmd = _command(monkeypatch, domain="bim")

    assert cmd[cmd.index("--domain") + 1] == "bim"
    assert cmd[cmd.index("--save") + 1] == "/tmp/t.ifc"


def test_a_task_is_its_text_and_its_plan_by_absolute_path(tmp_path):
    folder = _task(tmp_path)

    intent, attachments = lane.bim_task(str(folder))

    assert intent == "Model the flat on the plan."
    assert attachments == [str(folder / "plan.png")]


def test_labels_are_read_back_from_an_earlier_sample(tmp_path):
    (tmp_path / "labels.txt").write_text("create_02 create_03\n")

    tasks = lane.sampled_bim_tasks("validation", None, str(tmp_path), str(tmp_path / "labels.txt"))

    assert [label for label, _ in tasks] == ["create_02", "create_03"]
    assert tasks[0][1] == os.path.join(lane.BIM_DIR, "create", "create_02")


def test_a_sample_is_drawn_from_the_split_and_its_labels_written(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "BIM_DIR", str(tmp_path / "bim"))
    (tmp_path / "bim").mkdir()
    (tmp_path / "bim" / "split.json").write_text(json.dumps(
        {"create": {"validation": ["create_01", "create_11", "create_15"], "test": []}}))

    tasks = lane.sampled_bim_tasks("validation", 2, str(tmp_path))

    labels = (tmp_path / "labels.txt").read_text().split()
    assert [label for label, _ in tasks] == labels and len(labels) == 2


def test_the_table_has_a_row_per_task_with_its_six_components_and_the_means_under_them():
    components = dict(counts=1.0, walls=0.5, spaces=0.5, hosting=0.0, openings=0.25, adjacency=0.75)
    rows = [{"label": "a", "passed": True, "score": 0.5, "components": components, "minutes": 10.0, "usd": 1.0},
            {"label": "b", "passed": False, "score": 0.0, "components": dict.fromkeys(components, 0.0),
             "minutes": 20.0, "usd": 3.0}]

    lines = lane.bim_table(rows).splitlines()

    assert all(name in lines[0] for name in ("task", "Assertion pass", "score", "counts", "walls", "spaces",
                                              "hosting", "openings", "adjacency", "min", "$"))
    assert "yes" in lines[2] and "no" in lines[3]
    assert lines[4].startswith("| mean") and "50%" in lines[4] and "15.0" in lines[4] and "2.00" in lines[4]
