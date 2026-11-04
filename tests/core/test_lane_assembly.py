"""
Level 1 -- `tools/lane.py --domain assembly` runs a task of the assembly benchmark's from-parts mode through the Assembly Domain and tabulates what came of it.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402


def _task(root, label="from_parts_01"):
    folder = root / "from_parts" / label
    (folder / "parts").mkdir(parents=True)
    for name in ("Part1", "Part2"):
        (folder / "parts" / (name + ".step")).write_text("step")
    (folder / "task.json").write_text(json.dumps({
        "text": "Create one Assembly from the 2 part files in parts/.",
        "inputs": {"parts": [{"label": "Part1", "file": "parts/Part1.step"},
                             {"label": "Part2", "file": "parts/Part2.step"}], "ground": "Part1"}}))
    (folder / "reference.FCStd").write_text("reference")
    return folder


def _command(monkeypatch, **extra):
    seen = []
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: seen.append(cmd))
    lane.run_one("t", "", "opus", "medium", 8.0, "/tmp/t.jsonl", "Do it.", "Claude (subscription)",
                 **extra)
    return seen[0]


def test_the_part_command_line_has_no_domain_flag(monkeypatch):
    assert "--domain" not in _command(monkeypatch)


def test_an_assembly_run_names_its_domain_and_saves_an_fcstd(monkeypatch):
    cmd = _command(monkeypatch, domain="assembly")

    assert cmd[cmd.index("--domain") + 1] == "assembly"
    assert cmd[cmd.index("--save") + 1] == "/tmp/t.FCStd"


def test_a_task_is_its_text_and_its_part_files_by_absolute_path(tmp_path):
    folder = _task(tmp_path)

    intent, attachments = lane.assembly_task(str(folder))

    assert intent == "Create one Assembly from the 2 part files in parts/."
    assert attachments == [str(folder / "parts" / "Part1.step"), str(folder / "parts" / "Part2.step")]


def test_sample_draws_from_the_split_of_the_assembly_mode(monkeypatch, tmp_path):
    for label in ("from_parts_01", "from_parts_02", "from_parts_03"):
        _task(tmp_path, label)
    (tmp_path / "split.json").write_text(json.dumps({"from_parts": {
        "validation": ["from_parts_01"], "test": ["from_parts_02", "from_parts_03"]}}))
    monkeypatch.setattr(lane, "ASSEMBLY_DIR", str(tmp_path))
    out = tmp_path / "out"
    out.mkdir()

    tasks = lane.sampled_assembly_tasks("test", 2, str(out))

    assert sorted(label for label, _ in tasks) == ["from_parts_02", "from_parts_03"]
    assert tasks[0][1] == str(tmp_path / "from_parts" / tasks[0][0])
    assert (out / "labels.txt").read_text().split() == [label for label, _ in tasks]


def test_sweep_hands_the_domain_the_text_and_the_parts_to_every_run(monkeypatch, tmp_path):
    folder = _task(tmp_path)
    seen = []

    class Done:
        returncode = 0

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, **kwargs):
        seen.append((intent, kwargs))
        open(log[:-len(".jsonl")] + ".FCStd", "w").write("saved")
        open(log, "w").close()
        return Done()

    monkeypatch.setattr(lane, "run_one", fake_run_one)
    monkeypatch.setattr(lane, "watch", lambda *a, **k: "exited")

    lane.sweep([("from_parts_01", "")], ["opus"], "medium", 8.0, "unused", str(tmp_path / "out"),
               1800, 1, False, "Claude (subscription)", None, domain="assembly",
               assemblies={"from_parts_01": str(folder)})

    intent, kwargs = seen[0]
    assert intent == "Create one Assembly from the 2 part files in parts/."
    assert kwargs["domain"] == "assembly"
    assert kwargs["attach"] == [str(folder / "parts" / "Part1.step"), str(folder / "parts" / "Part2.step")]
    state = json.loads((tmp_path / "out" / "state.json").read_text())
    assert state["runs"]["from_parts_01/opus"]["returncode"] == 0


def test_the_parser_reads_the_domain_and_the_assembly_tasks(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(lane, "sweep", lambda *a, **k: captured.update(k=k, a=a))
    monkeypatch.setattr(sys, "argv", ["lane.py", "--model", "opus", "--domain", "assembly",
                                      "--assembly-task", "from_parts_01=/d/from_parts_01",
                                      "--out", str(tmp_path)])

    lane.main()

    assert captured["k"]["domain"] == "assembly"
    assert captured["k"]["assemblies"] == {"from_parts_01": "/d/from_parts_01"}


def test_the_table_has_a_row_per_task_and_the_means_under_it():
    rows = [{"label": "a", "passed": True, "score": 0.5, "minutes": 10.0, "usd": 2.0},
            {"label": "b", "passed": False, "score": 0.0, "minutes": 20.0, "usd": 4.0}]

    table = lane.assembly_table(rows).splitlines()

    assert table[0].startswith("| task | Assertion pass | score | parts ok | min | $ |")
    assert "| a | yes | 0.5000 | - | 10.0 | 2.00 |" in table
    assert "| b | no | 0.0000 | - | 20.0 | 4.00 |" in table
    assert table[-1] == "| mean | 50% | 0.2500 | - | 15.0 | 3.00 |"


def _scratch(root, label="from_scratch_01"):
    folder = root / "from_scratch" / label
    folder.mkdir(parents=True)
    graph = {"nodes": [{"name": "Part1", "size_mm": [10, 10, 5], "volume_mm3": 500.0, "faces": 6,
                        "bbox_mm": [0, 0, 0, 10, 10, 5]}], "edges": [], "fixed": "Part1"}
    (folder / "task.json").write_text(json.dumps({
        "text": "Model every part of the graph.", "inputs": {"graph": graph}}))
    return folder, graph


def test_a_from_scratch_task_is_its_text_and_its_graph_with_no_part_files(tmp_path):
    folder, graph = _scratch(tmp_path)

    intent, attachments = lane.assembly_task(str(folder))

    assert intent.startswith("Model every part of the graph.")
    assert json.loads(intent[intent.index("{"):]) == graph
    assert attachments == []


def test_sample_draws_from_the_mode_it_is_given(monkeypatch, tmp_path):
    _task(tmp_path, "from_parts_01")
    _scratch(tmp_path, "from_scratch_01")
    (tmp_path / "split.json").write_text(json.dumps({
        "from_parts": {"validation": ["from_parts_01"], "test": []},
        "from_scratch": {"validation": ["from_scratch_01"], "test": []}}))
    monkeypatch.setattr(lane, "ASSEMBLY_DIR", str(tmp_path))
    out = tmp_path / "out"
    out.mkdir()

    tasks = lane.sampled_assembly_tasks("validation", 1, str(out), mode="from_scratch")

    assert tasks == [("from_scratch_01", str(tmp_path / "from_scratch" / "from_scratch_01"))]


def test_the_table_counts_the_parts_that_built_ok():
    rows = [{"label": "a", "passed": True, "score": 0.5, "minutes": 10.0, "usd": 2.0, "parts": (2, 3)},
            {"label": "b", "passed": False, "score": 0.0, "minutes": 20.0, "usd": 4.0, "parts": (1, 2)}]

    table = lane.assembly_table(rows).splitlines()

    assert "| a | yes | 0.5000 | 2/3 | 10.0 | 2.00 |" in table
    assert "| mean | 50% | 0.2500 | 60% | 15.0 | 3.00 |" in table


def test_the_parts_that_built_ok_are_counted_off_the_build_part_results_in_the_log(tmp_path):
    log = tmp_path / "run.jsonl"
    result = lambda ok: json.dumps({"kind": "tool_result", "at": 0, "data": {"name": "build_part", "result": {"ok": ok}}})
    log.write_text("\n".join([result(True), json.dumps({"kind": "cost", "at": 0, "data": {}}), result(False), result(True)]))

    assert lane.parts_built(str(log)) == (2, 3)
