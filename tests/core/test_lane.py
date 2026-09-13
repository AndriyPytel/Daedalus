"""
Level 1 -- `tools/lane.py`'s pure-Python pieces, no FreeCAD needed: the log
rotation a retry depends on, the hang watchdog, and `--status`'s table.
"""
import glob
import json
import os
import subprocess
import sys
import time

import pytest

from tests import modelmania_harness

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402


def test_rotate_moves_an_existing_log_aside_instead_of_letting_a_retry_append(tmp_path):
    stem = str(tmp_path / "mm25-sonnet")
    open(stem + ".jsonl", "w").write("first\n")

    lane.rotate(stem)

    assert not os.path.exists(stem + ".jsonl")
    assert open(stem + ".attempt1.jsonl").read() == "first\n"

    open(stem + ".jsonl", "w").write("second\n")
    lane.rotate(stem)

    assert open(stem + ".attempt1.jsonl").read() == "first\n"
    assert open(stem + ".attempt2.jsonl").read() == "second\n"


def test_a_run_that_never_appends_leaves_one_log_per_attempt(tmp_path):
    stem = str(tmp_path / "mm24-opus")
    for content in ("attempt-a\n", "attempt-b\n", "attempt-c\n"):
        lane.rotate(stem)
        open(stem + ".jsonl", "w").write(content)

    logs = sorted(glob.glob(str(tmp_path / "mm24-opus*.jsonl")))
    assert [open(path).read() for path in logs] == ["attempt-a\n", "attempt-b\n", "attempt-c\n"]


def test_watch_kills_a_process_whose_log_stops_growing(tmp_path):
    log = str(tmp_path / "run.jsonl")
    open(log, "w").close()
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    clock = {"t": 0.0}
    outcome = lane.watch(process, log, hang_seconds=5,
                         poll_seconds=0, sleep=lambda s: clock.__setitem__("t", clock["t"] + 1),
                         now=lambda: clock["t"])

    assert outcome == "hung"
    assert process.poll() is not None


def test_watch_does_not_kill_a_process_whose_log_keeps_growing(tmp_path):
    log = str(tmp_path / "run.jsonl")
    process = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(0.3)"])

    def growing_sleep(seconds, log=log):
        with open(log, "a") as handle:
            handle.write("x")

    outcome = lane.watch(process, log, hang_seconds=1000, poll_seconds=0.05, sleep=growing_sleep)

    assert outcome == "exited"


def test_status_prints_the_same_table_compare_builds(tmp_path, capsys):
    fixtures = modelmania_harness.RECORDED_DIR / "sessions"
    out = tmp_path
    for model, fixture in (("haiku", "reached_verdict.jsonl"), ("opus", "blocked.jsonl")):
        model_dir = out / model
        model_dir.mkdir()
        with open(os.path.join(fixtures, fixture)) as src:
            (model_dir / "mm25.jsonl").write_text(src.read())

    lane.status(str(out))

    from core.measure import compare
    expected = compare([str(out / "haiku" / "mm25.jsonl")],
                       [str(out / "opus" / "mm25.jsonl")], "haiku", "opus")
    assert capsys.readouterr().out.strip() == expected.strip()


def test_a_run_is_told_which_provider_sells_the_model(monkeypatch):
    """`run.py` resolves a model through the provider named to it, not a fetch."""
    spawned = {}

    def fake_popen(cmd):
        spawned["cmd"] = cmd
        return None

    monkeypatch.setattr(lane.subprocess, "Popen", fake_popen)

    lane.run_one("mm25", "sheet.pdf", "glm-5.3-flash", "medium", None,
                 "/tmp/mm25.jsonl", "Build it.", "OpenCode Go")

    cmd = spawned["cmd"]
    assert cmd[cmd.index("--provider") + 1] == "OpenCode Go"
    assert cmd[cmd.index("--model") + 1] == "glm-5.3-flash"


def test_a_task_with_no_sheet_attaches_nothing(monkeypatch):
    """A part named by a standard is an Intent with no page to read."""
    spawned = {}
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: spawned.setdefault("cmd", cmd))

    lane.run_one("bolt-nut", "", "sonnet", "medium", None, "/tmp/b.jsonl",
                 "Model an M12 nut.", "Claude (subscription)")

    assert "--attach" not in spawned["cmd"]


def test_a_run_that_never_stops_is_killed_on_the_wall_clock():
    """
    A gateway model's `cost` events carry no `usd`, so `--budget-usd` cannot
    trip and only the clock can end a run that keeps talking.
    """
    clock = [0.0]

    class Endless:
        returncode = None

        def poll(self):
            return None

        def kill(self):
            self.returncode = -9

        def wait(self):
            return self.returncode

    process = Endless()
    outcome = lane.watch(process, "/nonexistent.jsonl", hang_seconds=1e9, run_seconds=60,
                         poll_seconds=1, sleep=lambda seconds: clock.__setitem__(0, clock[0] + 10),
                         now=lambda: clock[0])

    assert outcome == "over time"
    assert process.returncode == -9


def test_status_runs_as_a_standalone_script_not_only_imported(tmp_path):
    """
    `lane.py --status` shells out to `core.measure` without a `sys.path`
    bootstrap, so invoking it the way `--baseline` and a human both would --
    `python src/tools/lane.py ...` from the repo root -- raised
    `ModuleNotFoundError: No module named 'core'` before this fixed it.
    """
    fixtures = modelmania_harness.RECORDED_DIR / "sessions"
    model_dir = tmp_path / "haiku"
    model_dir.mkdir()
    with open(os.path.join(fixtures, "reached_verdict.jsonl")) as src:
        (model_dir / "mm25.jsonl").write_text(src.read())

    lane_path = os.path.join(ROOT, "src", "tools", "lane.py")
    result = subprocess.run(
        [sys.executable, lane_path, "--status", "--out", str(tmp_path)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_baseline_tasks_walk_the_pinned_eight_sheets_twice_as_replicates():
    """
    FCC-294: the lane walks each of the 8 pinned sheets twice under the same
    model -- two replicates giving a spread, not an `off`/`on` comparison --
    so its own `--baseline` and `report.py`'s reading of the result cannot
    drift onto a different set of sheets than FCC-319 measures against.
    """
    tasks = lane.baseline_tasks()

    labels = [label for label, _sheet in tasks]
    assert len(tasks) == 16
    assert sorted(labels) == sorted(
        "%s-rep%d" % (stem, rep)
        for stem in ("mm2023-phase1", "mm2023-phase2", "mm2024-phase1", "mm2024-phase2",
                     "mm2025-phase1", "mm2025-phase2", "model-mania-2024",
                     "model-mania_-in-person-2023")
        for rep in (1, 2)
    )
    assert len(set(labels)) == 16


def test_baseline_needs_exactly_one_model(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--baseline", "--model", "sonnet", "--model", "opus", "--out", "/tmp/x",
    ])

    with pytest.raises(SystemExit):
        lane.main()


def _write_events(path, events):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for at, kind, data in events:
            handle.write(json.dumps({"kind": kind, "at": at, "data": data}) + "\n")


def test_report_marks_a_log_with_no_events_failed_to_start(tmp_path):
    _write_events(str(tmp_path / "haiku" / "mm25.jsonl"), [])

    text = lane.report(str(tmp_path))

    assert "failed to start" in text


def test_report_marks_an_unparseable_log_failed_to_start(tmp_path):
    path = tmp_path / "haiku" / "mm25.jsonl"
    os.makedirs(path.parent)
    path.write_text("{not json\n")

    text = lane.report(str(tmp_path))

    assert "failed to start" in text


def test_report_sums_assertion_counts_across_events(tmp_path):
    _write_events(str(tmp_path / "haiku" / "mm25.jsonl"), [
        (1, "assertion", {"confirmed": ["a", "b"], "unmet": ["c"]}),
        (2, "assertion", {"confirmed": ["d"], "unmet": []}),
        (3, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = lane.report(str(tmp_path))

    assert "3/1" in text


def test_report_sorts_models_by_pass_rate_descending(tmp_path):
    _write_events(str(tmp_path / "weak" / "mm25.jsonl"), [
        (1, "review_verdict", {"verdict": "fail", "reason": "no"}),
    ])
    _write_events(str(tmp_path / "strong" / "mm25.jsonl"), [
        (1, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = lane.report(str(tmp_path))
    summary = text.split("## Summary")[1].split("## Detail")[0]

    assert summary.index("strong") < summary.index("weak")


def test_report_with_no_logs_says_so_plainly(tmp_path):
    text = lane.report(str(tmp_path))

    assert "no sweep logs found" in text


def test_report_sums_question_count_in_summary_and_shows_it_in_detail(tmp_path):
    _write_events(str(tmp_path / "haiku" / "mm25.jsonl"), [
        (1, "question", {"text": "Which hole pattern?"}),
        (2, "answer", {"text": "4x M6"}),
        (3, "question", {"text": "Material?"}),
        (4, "answer", {"text": "6061-T6"}),
        (5, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = lane.report(str(tmp_path))
    summary = text.split("## Summary")[1].split("## Detail")[0]
    detail = text.split("## Detail")[1]

    assert "2.0" in summary
    assert "questions" in detail
