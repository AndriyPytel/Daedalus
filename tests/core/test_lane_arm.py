"""
Level 1 -- `tools/lane.py --arm` reaches `tools/run.py`, and the default arm's
command line is the one it always was.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402


def _command(monkeypatch, **extra):
    seen = []
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: seen.append(cmd))
    lane.run_one("101", "a.png", "opus", "medium", 5.0, "/tmp/101.jsonl", "Do it.",
                 "Claude (subscription)", **extra)
    return seen[0]


def test_the_pipeline_command_line_has_no_arm_flag(monkeypatch):
    assert "--arm" not in _command(monkeypatch)
    assert "--arm" not in _command(monkeypatch, arm="pipeline")


def test_another_arm_is_passed_on_with_its_turn_ceiling(monkeypatch):
    cmd = _command(monkeypatch, arm="reader", max_turns=30)

    assert cmd[cmd.index("--arm") + 1] == "reader"
    assert cmd[cmd.index("--max-turns") + 1] == "30"
    assert cmd[cmd.index("--budget-usd") + 1] == "5.0"


def test_sweep_hands_the_arm_to_every_run(monkeypatch, tmp_path):
    arms = []

    class Done:
        returncode = 0

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, **kwargs):
        arms.append(kwargs.get("arm"))
        open(log, "w").close()
        return Done()

    monkeypatch.setattr(lane, "run_one", fake_run_one)
    monkeypatch.setattr(lane, "watch", lambda *a, **k: "exited")

    lane.sweep([("101", "a.png")], ["opus"], "medium", None, "Do it.", str(tmp_path), 1800, 1,
               False, "Claude (subscription)", None, arm="oneshot")

    assert arms == ["oneshot"]


def test_the_parser_offers_the_four_arms(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(lane, "sweep", lambda *a, **k: captured.update(k=k))
    monkeypatch.setattr(sys, "argv", ["lane.py", "--model", "opus", "--task", "1=a.png",
                                      "--arm", "nopreflight", "--out", str(tmp_path)])

    lane.main()

    assert captured["k"]["arm"] == "nopreflight"
