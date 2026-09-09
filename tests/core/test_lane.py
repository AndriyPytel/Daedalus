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
