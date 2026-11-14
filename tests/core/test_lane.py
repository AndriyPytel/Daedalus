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


def _exemplars(monkeypatch, tmp_path):
    monkeypatch.setenv("DAE_MODELMANIA_DIR", str(tmp_path))
    (tmp_path / "exemplar").mkdir(exist_ok=True)
    return tmp_path / "exemplar"


def test_a_retry_gets_its_own_stem_for_both_the_log_and_the_step(tmp_path):
    """
    A retry no longer overwrites the previous attempt's `.step` -- each of
    3 failing attempts leaves its own `.jsonl` and, from `run_one`'s own
    naming, its own `.step`, the two sharing one timestamp stem.
    """
    out = str(tmp_path)
    stamps = iter(["20260101-000001", "20260101-000002", "20260101-000003"])

    class FakeProcess:
        returncode = 1

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        open(log, "w").close()
        open(log[: -len(".jsonl")] + ".step", "w").close()
        return FakeProcess()

    lane.sweep([("mm24-opus", "sheet.pdf")], ["opus"], "medium", None, "Build it.", out,
               1800, 3, False, "Claude (subscription)", None,
               timestamp=lambda: next(stamps), run_one=fake_run_one, watch=lambda *a, **k: "exited")

    jsonls = sorted(glob.glob(os.path.join(out, "opus", "mm24-opus.*.jsonl")))
    steps = sorted(glob.glob(os.path.join(out, "opus", "mm24-opus.*.step")))
    assert len(jsonls) == 3, jsonls
    assert [p[: -len(".jsonl")] for p in jsonls] == [p[: -len(".step")] for p in steps]

    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        state = json.load(handle)
    run = state["runs"]["mm24-opus/opus"]
    assert run["log"] == jsonls[-1]
    assert run["attempts"] == jsonls


def test_logs_reads_the_final_attempt_from_state_json_not_every_retry(tmp_path):
    """
    Two failed attempts at the same key must not turn into two rows of
    `report()` -- `state.json` names which attempt was final, and that is
    the one `_logs()` must yield.
    """
    out = str(tmp_path)
    os.makedirs(os.path.join(out, "opus"))
    winner = os.path.join(out, "opus", "mm24-opus.20260101-000002.jsonl")
    loser = os.path.join(out, "opus", "mm24-opus.20260101-000001.jsonl")
    open(winner, "w").close()
    open(loser, "w").close()
    state = {"runs": {"mm24-opus/opus": {"label": "mm24-opus", "model": "opus",
             "log": winner, "attempts": [loser, winner], "status": "done"}}}
    with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)

    rows = list(lane._logs(out))

    assert rows == [("opus", "mm24-opus", winner)]


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


def test_a_run_is_told_which_provider_sells_the_model():
    """`run.py` resolves a model through the provider named to it, not a fetch."""
    cmd = lane.run_command("sheet.pdf", "glm-5.3-flash", "medium", None,
                           "/tmp/mm25.jsonl", "Build it.", "OpenCode Go")

    assert cmd[cmd.index("--provider") + 1] == "OpenCode Go"
    assert cmd[cmd.index("--model") + 1] == "glm-5.3-flash"


def test_a_run_is_given_its_task_and_where_to_save():
    cmd = lane.run_command("/data/sheet.pdf", "sonnet", "medium", 5.0, "/tmp/a.jsonl",
                           "Build it.", "Claude (subscription)")

    assert cmd[cmd.index("--task") + 1] == "/data/sheet.pdf"
    assert cmd[cmd.index("--intent") + 1] == "Build it."
    assert cmd[cmd.index("--save") + 1] == "/tmp/a.step"
    assert cmd[cmd.index("--budget-usd") + 1] == "5.0"
    assert "--native" not in cmd


def test_a_task_with_no_sheet_is_an_intent_alone():
    """A part named by a standard is an Intent with no page to read."""
    cmd = lane.run_command("", "sonnet", "medium", None, "/tmp/b.jsonl",
                           "Model an M12 nut.", "Claude (subscription)")

    assert "--task" not in cmd
    assert cmd[cmd.index("--intent") + 1] == "Model an M12 nut."


def test_a_run_saves_the_file_its_task_leaves(tmp_path):
    folder = tmp_path / "from_scratch_01"
    folder.mkdir()
    (folder / "task.json").write_text(json.dumps({"text": "Model it.", "inputs": {"graph": {}}}))

    cmd = lane.run_command(str(folder), "sonnet", "medium", None, "/tmp/c.jsonl", "unused",
                           "Claude (subscription)")

    assert cmd[cmd.index("--save") + 1] == "/tmp/c.FCStd"
    assert cmd[cmd.index("--task") + 1] == str(folder)


def test_sweep_runs_every_task_for_every_model_through_the_run_one_it_is_given(tmp_path):
    ran = []

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

    def fake_run_one(label, path, model, effort, budget_usd, log, intent, provider, native=False):
        ran.append((label, path, model, intent))
        open(log, "w").close()
        open(log[: -len(".jsonl")] + ".step", "w").close()
        return FakeProcess()

    stopped = lane.sweep([("a", "a.pdf"), ("b", "")], ["opus", "sonnet"], "medium", None, "Build it.",
                         str(tmp_path), 1800, 1, False, "Claude (subscription)", None,
                         run_one=fake_run_one)

    assert not stopped
    assert ran == [("a", "a.pdf", "opus", "Build it."), ("b", "", "opus", "Build it."),
                   ("a", "a.pdf", "sonnet", "Build it."), ("b", "", "sonnet", "Build it.")]


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
    exemplars = _exemplars(monkeypatch, tmp_path)
    exemplar = exemplars / "mm2025-phase1.step"
    exemplar.write_text("not a real step")
    log = str(tmp_path / "mm2025-phase1-rep1.jsonl")
    open(log, "w").close()

    assert lane._score_for(log, "mm2025-phase1-rep1") is None


def test_score_for_reads_the_scorer_when_both_files_exist(tmp_path, monkeypatch):
    exemplars = _exemplars(monkeypatch, tmp_path)
    (exemplars / "mm2025-phase1.step").write_text("exemplar")
    log = str(tmp_path / "mm2025-phase1-rep1.jsonl")
    open(log, "w").close()
    (tmp_path / "mm2025-phase1-rep1.step").write_text("candidate")

    import score as score_module
    monkeypatch.setattr(score_module, "score", lambda candidate, exemplar: {"score": 0.5})

    assert lane._score_for(log, "mm2025-phase1-rep1") == 0.5


def test_sheet_stem_keeps_an_arm_suffix_past_the_repeat():
    assert lane._sheet_stem("mm2025-phase1-rep2-off") == "mm2025-phase1-off"
    assert lane._sheet_stem("mm2025-phase1-rep2") == "mm2025-phase1"


def test_exemplar_for_ignores_an_arm_suffix(monkeypatch, tmp_path):
    exemplars = _exemplars(monkeypatch, tmp_path)
    (exemplars / "mm2025-phase1.step").write_text("exemplar")

    assert lane._exemplar_for("mm2025-phase1-rep1-off") == str(exemplars / "mm2025-phase1.step")


def test_smoke_tasks_takes_a_sheet_argument():
    tasks = lane.smoke_tasks("mm2024-phase1.pdf")

    assert len(tasks) == 1
    label, sheet = tasks[0]
    assert label == "mm2024-phase1"
    assert sheet == str(modelmania_harness.PHASES["mm2024-phase1.pdf"])


def test_smoke_cli_selects_mm2024_phase1(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--smoke", "--smoke-sheet", "mm2024-phase1.pdf",
        "--model", "sonnet", "--budget-usd", "5", "--out", str(tmp_path),
    ])
    called = {}

    def fake_sweep(tasks, *args, **kwargs):
        called["tasks"] = tasks
        return None

    lane.main(sweep=fake_sweep)

    assert len(called["tasks"]) == 1
    label, sheet = called["tasks"][0]
    assert sheet.endswith("mm2024-phase1.pdf")


def test_pinned_needs_exactly_one_model(monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--pinned", "--model", "sonnet", "--model", "opus", "--out", "/tmp/x",
    ])

    with pytest.raises(SystemExit) as excinfo:
        lane.main()
    assert "exactly one --model" in str(excinfo.value)


def test_sweep_resumes_by_skipping_a_run_state_already_marks_done(tmp_path):
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

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append(label)
        open(log, "w").close()
        open(log[: -len(".jsonl")] + ".step", "w").write("ISO-10303-21;")
        return FakeProcess()

    tasks = [("mm2025-phase1-rep1", "sheet1.pdf"), ("mm2025-phase1-rep2", "sheet1.pdf")]
    lane.sweep(tasks, ["opus"], "medium", None, "Build it.", out, 1800, 3, True,
              "Claude (subscription)", None, run_one=fake_run_one, watch=lambda *a, **k: "exited")

    assert spawned == ["mm2025-phase1-rep2"]
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        final_state = json.load(handle)
    assert final_state["runs"]["mm2025-phase1-rep1/opus"]["status"] == "done"
    assert final_state["runs"]["mm2025-phase1-rep2/opus"]["status"] == "done"


def test_report_states_the_noise_floor_from_repeats_of_the_same_sheet(tmp_path, monkeypatch):
    exemplars = _exemplars(monkeypatch, tmp_path)
    (exemplars / "mm2025-phase1.step").write_text("exemplar")
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

    def fake_sweep(tasks, models, effort, budget_usd, *rest, **kwargs):
        captured["budget_usd"] = budget_usd

    monkeypatch.setattr(sys, "argv", ["lane.py", "--pinned", "--model", "opus", "--out", str(tmp_path)])

    lane.main(sweep=fake_sweep)

    assert captured["budget_usd"] == lane.PINNED_BUDGET_USD


def test_an_explicit_budget_overrides_the_pinned_default(monkeypatch, tmp_path):
    captured = {}

    def fake_sweep(tasks, models, effort, budget_usd, *rest, **kwargs):
        captured["budget_usd"] = budget_usd

    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--pinned", "--model", "opus", "--budget-usd", "55", "--out", str(tmp_path),
    ])

    lane.main(sweep=fake_sweep)

    assert captured["budget_usd"] == 55.0


def _quota_log(path, resets_at=1791057600):
    _write_events(path, [(1.0, "blocked", {
        "reason": "opus refused the request: the subscription's seven-day limit is spent, "
                  "and it resets at 23:00. Wait for it, or run this Intent on a provider with a key.",
        "provider_said": json.dumps({"status": "rejected", "resetsAt": resets_at,
                                     "rateLimitType": "seven_day"}),
        "tried": []})])


def test_a_quota_refusal_is_not_retried_and_stops_the_sweep_with_its_reset_time(
        tmp_path, capsys):
    out = str(tmp_path)
    spawned = []

    class FakeProcess:
        returncode = 1

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append(label)
        _quota_log(log)
        return FakeProcess()

    stopped = lane.sweep([("126", "a.pdf"), ("142", "b.pdf")], ["opus"], "medium", None,
                         "Build it.", out, 1800, 3, False, "Claude (subscription)", None, run_one=fake_run_one, watch=lambda *a, **k: "exited")

    assert spawned == ["126"]
    assert stopped
    assert "resets at 23:00" in capsys.readouterr().out
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        run = json.load(handle)["runs"]["126/opus"]
    assert run["status"] == "quota"


def test_a_quota_stopped_run_is_run_again_by_resume(tmp_path):
    out = str(tmp_path)
    os.makedirs(os.path.join(out, "opus"))
    state = {"runs": {"126/opus": {"label": "126", "model": "opus", "log": "x.jsonl",
                                   "status": "quota"}}}
    with open(os.path.join(out, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    spawned = []

    class FakeProcess:
        returncode = 1

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append(label)
        open(log, "w").close()
        return FakeProcess()

    lane.sweep([("126", "a.pdf")], ["opus"], "medium", None, "Build it.", out, 1800, 1, True,
               "Claude (subscription)", None, run_one=fake_run_one, watch=lambda *a, **k: "exited")

    assert spawned == ["126"]


def test_an_ordinary_refusal_is_still_retried(tmp_path):
    out = str(tmp_path)
    spawned = []

    class FakeProcess:
        returncode = 1

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append(label)
        _write_events(log, [(1.0, "blocked", {"reason": "opus refused the request: you are "
                                                        "being rate limited", "tried": []})])
        return FakeProcess()

    assert not lane.sweep([("126", "a.pdf")], ["opus"], "medium", None, "Build it.", out, 1800,
                          3, False, "Claude (subscription)", None, run_one=fake_run_one, watch=lambda *a, **k: "exited")
    assert spawned == ["126"] * 3


def test_a_run_that_exits_0_without_a_step_is_recorded_failed(tmp_path):
    out = str(tmp_path)

    class FakeProcess:
        returncode = 0

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        open(log, "w").close()
        return FakeProcess()

    lane.sweep([("126", "a.pdf")], ["opus"], "medium", None, "Build it.", out, 1800, 1, False,
               "Claude (subscription)", None, run_one=fake_run_one, watch=lambda *a, **k: "exited")

    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        assert json.load(handle)["runs"]["126/opus"]["status"] == "failed"


def test_an_attempt_timestamp_survives_freecad_binding_datetime_to_the_class_in_main(monkeypatch):
    """FreeCAD's own init runs `from datetime import datetime` in `__main__`, which is the lane when it runs as a script."""
    import datetime
    import re

    monkeypatch.setitem(vars(lane), "datetime", datetime.datetime)

    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}", lane._attempt_timestamp())


def test_a_disputed_end_is_reported_unverified_and_is_not_a_blocked_run(tmp_path):
    from core.measure import measure

    path = str(tmp_path / "haiku" / "mm25.jsonl")
    _write_events(path, [
        (1, "assertion", {"confirmed": [], "unmet": ["bore through-count"]}),
        (2, "disputed", {"entries": ["bore through-count"], "measured": ["count 2"],
                         "because": ["wrong"]}),
    ])

    detail = lane.report(str(tmp_path)).split("## Detail")[1]

    assert measure(path).blocked_reason is None
    assert measure(path).disputed == ["bore through-count"]
    assert "unverified" in detail
    assert "blocked" not in detail
    assert "bore through-count" in detail


def _main_with(monkeypatch, tmp_path, *flags):
    monkeypatch.setattr(sys, "argv", ["lane.py", "--model", "opus", "--task", "a=a.pdf",
                                      "--out", str(tmp_path), *flags])
    return lambda: lane.main(sweep=lambda *a, **k: pytest.fail("sweep must not start"))


def test_a_paid_run_without_a_budget_refuses_to_start(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        _main_with(monkeypatch, tmp_path)()

    assert stopped.value.code == 2
    assert "--budget-usd" in capsys.readouterr().err


def test_retries_below_1_is_rejected_with_a_message(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        _main_with(monkeypatch, tmp_path, "--budget-usd", "5", "--retries", "0")()

    assert stopped.value.code == 2
    assert "--retries" in capsys.readouterr().err


def test_help_says_the_budget_is_checked_between_calls(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["lane.py", "--help"])

    with pytest.raises(SystemExit):
        lane.main()

    assert "between calls" in " ".join(capsys.readouterr().out.split())


def _sweep_once(monkeypatch, tmp_path, outcome, events, returncode):
    spawned = []

    class FakeProcess:
        pass

    FakeProcess.returncode = returncode

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append(label)
        _write_events(log, events)
        return FakeProcess()

    lane.sweep([("126", "a.pdf")], ["opus"], "medium", 3.0, "Build it.", str(tmp_path), 1800,
               3, False, "Claude (subscription)", 60.0, run_one=fake_run_one, watch=lambda *a, **k: outcome)
    with open(os.path.join(str(tmp_path), "state.json"), encoding="utf-8") as handle:
        return spawned, json.load(handle)["runs"]["126/opus"]


def test_a_run_stopped_by_the_time_ceiling_is_not_retried(monkeypatch, tmp_path):
    spawned, run = _sweep_once(monkeypatch, tmp_path, "over time", [], -9)

    assert spawned == ["126"]
    assert run["status"] == "ceiling"


def test_a_run_stopped_by_the_dollar_ceiling_is_not_retried(monkeypatch, tmp_path):
    ceiling = [(1.0, "budget_exhausted", {"reason": "the dollar ceiling was reached", "tried": []})]

    spawned, run = _sweep_once(monkeypatch, tmp_path, "exited", ceiling, 1)

    assert spawned == ["126"]
    assert run["status"] == "ceiling"


def test_a_crash_and_a_hang_are_still_retried(monkeypatch, tmp_path):
    assert _sweep_once(monkeypatch, tmp_path / "a", "exited", [], 1)[0] == ["126"] * 3
    assert _sweep_once(monkeypatch, tmp_path / "b", "hung", [], -9)[0] == ["126"] * 3


def test_report_detail_names_the_model_each_run_was_answered_by(tmp_path):
    _write_events(str(tmp_path / "opus" / "mm25.jsonl"), [
        (1, "cost", {"model": "opus", "resolved_model": "claude-opus-5-5", "usd": 0.1}),
        (2, "cost", {"model": "opus", "resolved_model": "claude-opus-5-5", "usd": 0.1}),
    ])

    detail = lane.report(str(tmp_path)).split("## Detail")[1]

    assert "resolved model" in detail
    assert "claude-opus-5-5" in detail


def _attempt_log(applied, completed, saved):
    from core.events import APPLIED, COMPLETED, FAILED, EventStream

    stream = EventStream()
    for index in range(applied):
        stream.emit(APPLIED, step=index + 1)
    stream.emit(COMPLETED, summary="done") if completed else stream.emit(FAILED, message="stalled")
    return stream, saved


def _sweep_of_attempts(tmp_path, attempts):
    out = str(tmp_path)
    stamps = iter("20260101-00000%d" % n for n in range(1, len(attempts) + 1))
    todo = iter(attempts)

    class FakeProcess:
        returncode = 1

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, **kwargs):
        stream, saved = next(todo)
        with open(log, "w", encoding="utf-8") as handle:
            handle.write("\n".join(event.as_json() for event in stream) + "\n")
        if saved:
            with open(log[: -len(".jsonl")] + ".step", "w") as handle:
                handle.write("ISO-10303-21;")
        return FakeProcess()

    lane.sweep([("t", "sheet.pdf")], ["opus"], "medium", None, "Build it.", out, 1800,
               len(attempts), False, "Claude (subscription)", None, timestamp=lambda: next(stamps),
               run_one=fake_run_one, watch=lambda *a, **k: "exited")
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        return json.load(handle)["runs"]["t/opus"]


def test_a_retry_that_saved_nothing_does_not_replace_an_earlier_attempt_that_saved_a_step(tmp_path):
    run = _sweep_of_attempts(tmp_path, [_attempt_log(2, False, True), _attempt_log(0, False, False)])

    assert run["log"] == run["attempts"][0]


def test_a_retry_that_applied_fewer_steps_does_not_replace_an_earlier_attempt(tmp_path):
    run = _sweep_of_attempts(tmp_path, [_attempt_log(3, False, True), _attempt_log(1, False, True)])

    assert run["log"] == run["attempts"][0]


def test_a_retry_that_applied_more_steps_replaces_an_earlier_attempt(tmp_path):
    run = _sweep_of_attempts(tmp_path, [_attempt_log(1, False, True), _attempt_log(3, False, True)])

    assert run["log"] == run["attempts"][1]


def test_an_attempt_the_agent_called_ok_beats_one_with_more_steps(tmp_path):
    run = _sweep_of_attempts(tmp_path, [_attempt_log(5, False, True), _attempt_log(1, True, True),
                                        _attempt_log(4, False, True)])

    assert run["log"] == run["attempts"][1]


def test_an_effort_the_dial_does_not_offer_is_an_argument_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [
        "lane.py", "--pinned", "--model", "opus", "--effort", "ultra", "--out", str(tmp_path),
    ])

    with pytest.raises(SystemExit) as stopped:
        lane.main(sweep=lambda *args, **kwargs: None)

    assert stopped.value.code == 2


def _hang_limit_of(monkeypatch, tmp_path, *flags):
    seen = {}
    monkeypatch.setattr(sys, "argv", ["lane.py", "--model", "opus", "--task", "a=a.pdf",
                                      "--budget-usd", "5", "--out", str(tmp_path), *flags])
    lane.main(sweep=lambda tasks, models, effort, budget, intent, out, hang_seconds, *a, **k: seen.update(hang=hang_seconds))
    return seen["hang"]


def test_the_default_hang_limit_outlasts_the_call_ceiling_of_every_effort(monkeypatch, tmp_path):
    from core.llm.effort_policy import _CEILING

    for effort, ceiling in _CEILING.items():
        hang = _hang_limit_of(monkeypatch, tmp_path, "--effort", effort)
        assert hang == max(lane.HANG_SECONDS, ceiling + 300)


def test_max_without_hang_seconds_waits_at_least_3900_seconds(monkeypatch, tmp_path):
    assert _hang_limit_of(monkeypatch, tmp_path, "--effort", "max") >= 3900


def test_a_hang_limit_below_the_call_ceiling_is_a_parser_error(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        _hang_limit_of(monkeypatch, tmp_path, "--effort", "max", "--hang-seconds", "1800")

    assert stopped.value.code == 2
    assert "--hang-seconds" in capsys.readouterr().err


def test_an_explicit_hang_limit_at_or_above_the_call_ceiling_is_kept(monkeypatch, tmp_path):
    assert _hang_limit_of(monkeypatch, tmp_path, "--effort", "max", "--hang-seconds", "3600") == 3600
    assert _hang_limit_of(monkeypatch, tmp_path, "--effort", "medium", "--hang-seconds", "1000") == 1000
