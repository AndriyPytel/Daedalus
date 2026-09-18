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
    The lane walks each of the 8 pinned sheets twice under the same
    model -- two replicates giving a spread, not an `off`/`on` comparison --
    so its own `--baseline` and `report.py`'s reading of the result cannot
    drift onto a different set of sheets than the modelmania comparison
    run measures against.
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


def test_report_shows_a_dash_for_a_sheet_with_no_exemplar(tmp_path):
    _write_events(str(tmp_path / "haiku" / "bolt-nut.jsonl"), [
        (1, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = lane.report(str(tmp_path))

    detail_row = next(line for line in text.split("## Detail")[1].splitlines() if "bolt-nut" in line)
    assert detail_row.rstrip().endswith("| - |")


def test_score_for_is_none_without_a_matching_exemplar(tmp_path):
    log = str(tmp_path / "bolt-nut.jsonl")
    open(log, "w").close()

    assert lane._score_for(log, "bolt-nut") is None


def test_score_for_is_none_without_a_saved_step(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "EXEMPLAR_DIR", str(tmp_path))
    exemplar = tmp_path / "mm2025-phase1.step"
    exemplar.write_text("not a real step")
    log = str(tmp_path / "mm2025-phase1-rep1.jsonl")
    open(log, "w").close()

    assert lane._score_for(log, "mm2025-phase1-rep1") is None


def test_score_for_reads_the_scorer_when_both_files_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "EXEMPLAR_DIR", str(tmp_path))
    (tmp_path / "mm2025-phase1.step").write_text("exemplar")
    log = str(tmp_path / "mm2025-phase1-rep1.jsonl")
    open(log, "w").close()
    (tmp_path / "mm2025-phase1-rep1.step").write_text("candidate")

    import score as score_module
    monkeypatch.setattr(score_module, "score", lambda candidate, exemplar: {"score": 0.5})

    assert lane._score_for(log, "mm2025-phase1-rep1") == 0.5


def test_run_one_passes_the_conflicts_flag_through(monkeypatch):
    """`--conflicts` reaches `tools/run.py`'s own subprocess, unset by default."""
    spawned = {}
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: spawned.setdefault("cmd", cmd))

    lane.run_one("mm23", "sheet.pdf", "opus", "medium", None, "/tmp/mm23.jsonl",
                "Build it.", "Claude (subscription)", conflicts="off")

    cmd = spawned["cmd"]
    assert cmd[cmd.index("--conflicts") + 1] == "off"


def test_run_one_omits_the_conflicts_flag_when_unset(monkeypatch):
    spawned = {}
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: spawned.setdefault("cmd", cmd))

    lane.run_one("mm23", "sheet.pdf", "opus", "medium", None, "/tmp/mm23.jsonl",
                "Build it.", "Claude (subscription)")

    assert "--conflicts" not in spawned["cmd"]


def test_sweep_suffixes_the_arm_onto_the_task_label(tmp_path):
    """
    `logs/ab-rlm-agentic/<model>/mm23-off.jsonl` is the naming an earlier
    A/B already left on disk; this sweep reuses it rather than
    inventing a second scheme.
    """
    out = str(tmp_path)
    spawned = []

    class FakeProcess:
        returncode = 0

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider,
                     conflicts=None):
        spawned.append((label, log, conflicts))
        open(log, "w").close()
        return FakeProcess()

    lane.run_one, real_run_one = fake_run_one, lane.run_one
    lane.watch, real_watch = lambda *a, **k: "exited", lane.watch
    try:
        lane.sweep([("mm23", "sheet.pdf")], ["opus"], "medium", None, "Build it.", out,
                  1800, 3, False, "Claude (subscription)", None, conflicts="off")
    finally:
        lane.run_one, lane.watch = real_run_one, real_watch

    ((label, log, conflicts),) = spawned
    assert label == "mm23"
    assert log == os.path.join(out, "opus", "mm23-off.jsonl")
    assert conflicts == "off"


def test_sheet_stem_keeps_an_arm_suffix_past_the_repeat():
    assert lane._sheet_stem("mm2025-phase1-rep2-off") == "mm2025-phase1-off"
    assert lane._sheet_stem("mm2025-phase1-rep2") == "mm2025-phase1"


def test_exemplar_for_ignores_an_arm_suffix(monkeypatch, tmp_path):
    monkeypatch.setattr(lane, "EXEMPLAR_DIR", str(tmp_path))
    (tmp_path / "mm2025-phase1.step").write_text("exemplar")

    assert lane._exemplar_for("mm2025-phase1-rep1-off") == str(tmp_path / "mm2025-phase1.step")


def test_pinned_tasks_walk_the_six_phase_sheets_three_times_each():
    """
    Three repeats is the point: four headless runs of one sheet took 27.4,
    32.5, 66.1 and 122.4 minutes on identical input, and one repeat would
    report that spread as a fact about the sheet rather than about the run.
    """
    tasks = lane.pinned_tasks()

    labels = [label for label, _sheet in tasks]
    assert len(tasks) == 18
    assert sorted(labels) == sorted(
        "%s-rep%d" % (stem, rep)
        for stem in ("mm2023-phase1", "mm2023-phase2", "mm2024-phase1", "mm2024-phase2",
                     "mm2025-phase1", "mm2025-phase2")
        for rep in (1, 2, 3)
    )
    assert len(set(labels)) == 18


def test_smoke_tasks_is_one_sheet_one_repeat():
    tasks = lane.smoke_tasks()

    assert len(tasks) == 1
    label, sheet = tasks[0]
    assert "-rep" not in label
    assert sheet.endswith(".pdf")


def test_pinned_needs_exactly_one_model(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--pinned", "--model", "sonnet", "--model", "opus", "--out", "/tmp/x",
    ])

    with pytest.raises(SystemExit) as excinfo:
        lane.main()
    assert "exactly one --model" in str(excinfo.value)


def test_smoke_needs_exactly_one_model(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--smoke", "--model", "sonnet", "--model", "opus", "--out", "/tmp/x",
    ])

    with pytest.raises(SystemExit) as excinfo:
        lane.main()
    assert "exactly one --model" in str(excinfo.value)


def test_sweep_resumes_by_skipping_a_run_state_already_marks_done(tmp_path, monkeypatch):
    """The resume path, against a recorded state file -- no real run is spawned for it."""
    out = str(tmp_path)
    os.makedirs(os.path.join(out, "opus"))
    state = {"runs": {"mm2025-phase1-rep1/opus": {"label": "mm2025-phase1-rep1",
                      "model": "opus", "log": "unused.jsonl", "status": "done"}}}
    with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)

    spawned = []

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider):
        spawned.append(label)
        open(log, "w").close()
        return FakeProcess()

    monkeypatch.setattr(lane, "run_one", fake_run_one)
    monkeypatch.setattr(lane, "watch", lambda *a, **k: "exited")

    tasks = [("mm2025-phase1-rep1", "sheet1.pdf"), ("mm2025-phase1-rep2", "sheet1.pdf")]
    lane.sweep(tasks, ["opus"], "medium", None, "Build it.", out, 1800, 3, True,
              "Claude (subscription)", None)

    assert spawned == ["mm2025-phase1-rep2"]
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        final_state = json.load(handle)
    assert final_state["runs"]["mm2025-phase1-rep1/opus"]["status"] == "done"
    assert final_state["runs"]["mm2025-phase1-rep2/opus"]["status"] == "done"


def test_report_states_the_noise_floor_from_repeats_of_the_same_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, "EXEMPLAR_DIR", str(tmp_path))
    (tmp_path / "mm2025-phase1.step").write_text("exemplar")
    for rep in (1, 2, 3):
        _write_events(str(tmp_path / "opus" / ("mm2025-phase1-rep%d.jsonl" % rep)),
                     [(1, "review_verdict", {"verdict": "pass", "reason": "ok"})])
        (tmp_path / "opus" / ("mm2025-phase1-rep%d.step" % rep)).write_text("candidate")

    import score as score_module
    scores = iter([0.90, 0.94, 0.97])
    monkeypatch.setattr(score_module, "score", lambda candidate, exemplar: {"score": next(scores)})

    text = lane.report(str(tmp_path))

    assert "## Noise floor" in text
    assert "0.0700" in text


def test_report_says_when_no_sheet_has_enough_repeats_to_measure_a_spread(tmp_path):
    _write_events(str(tmp_path / "opus" / "bolt-nut.jsonl"), [
        (1, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = lane.report(str(tmp_path))

    assert "not enough repeats" in text


def test_compare_marks_a_score_difference_smaller_than_the_floor_unmeasurable(tmp_path):
    baseline_log = str(tmp_path / "baseline.jsonl")
    experiment_log = str(tmp_path / "experiment.jsonl")
    _write_events(baseline_log, [(1, "review_verdict", {"verdict": "pass", "reason": "ok"})])
    _write_events(experiment_log, [(1, "review_verdict", {"verdict": "pass", "reason": "ok"})])

    from core.measure import compare

    table = compare([baseline_log], [experiment_log], "before", "after", score_floor=1.0)

    assert "unmeasurable" in table.lower()


def test_pinned_defaults_the_budget_ceiling_above_the_run_a_30_dollar_cap_cut_off(monkeypatch, tmp_path):
    captured = {}

    def fake_sweep(tasks, models, effort, budget_usd, *rest):
        captured["budget_usd"] = budget_usd

    monkeypatch.setattr(lane, "sweep", fake_sweep)
    monkeypatch.setattr(sys, "argv", ["lane.py", "--pinned", "--model", "opus", "--out", str(tmp_path)])

    lane.main()

    assert captured["budget_usd"] == lane.PINNED_BUDGET_USD


def test_an_explicit_budget_overrides_the_pinned_default(monkeypatch, tmp_path):
    captured = {}

    def fake_sweep(tasks, models, effort, budget_usd, *rest):
        captured["budget_usd"] = budget_usd

    monkeypatch.setattr(lane, "sweep", fake_sweep)
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--pinned", "--model", "opus", "--budget-usd", "55", "--out", str(tmp_path),
    ])

    lane.main()

    assert captured["budget_usd"] == 55.0
