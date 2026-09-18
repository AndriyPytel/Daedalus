"""
Level 1 -- `tools/score.py`'s own seam: which harness venv it shells out to,
and that a missing one degrades to no score rather than a failure.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import score  # noqa: E402


def test_a_missing_harness_venv_returns_none(tmp_path):
    missing = str(tmp_path / "no-such-python")

    assert score.score("candidate.step", "exemplar.step", python=missing) is None


def test_a_present_harness_venv_is_shelled_out_to_and_its_json_parsed(monkeypatch, tmp_path):
    python = tmp_path / "python"
    python.write_text("")
    captured = {}

    class FakeResult:
        returncode = 0
        stdout = json.dumps({"score": 0.6779, "is_valid": True})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr(score.subprocess, "run", fake_run)

    result = score.score("candidate.step", "exemplar.step", python=str(python))

    assert result == {"score": 0.6779, "is_valid": True}
    assert captured["cmd"][0] == str(python)
    assert captured["cmd"][captured["cmd"].index("--candidate") + 1] == "candidate.step"
    assert captured["cmd"][captured["cmd"].index("--exemplar") + 1] == "exemplar.step"


def test_a_nonzero_exit_raises_with_the_scorers_own_stderr(monkeypatch, tmp_path):
    python = tmp_path / "python"
    python.write_text("")

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "cadgenbench blew up"

    monkeypatch.setattr(score.subprocess, "run", lambda cmd, **kwargs: FakeResult())

    try:
        score.score("candidate.step", "exemplar.step", python=str(python))
    except RuntimeError as exc:
        assert "cadgenbench blew up" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_harness_python_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("CADGENBENCH_VENV", "/somewhere/python")

    assert score.harness_python() == "/somewhere/python"


def test_harness_python_defaults_beside_the_repo(monkeypatch):
    monkeypatch.delenv("CADGENBENCH_VENV", raising=False)

    assert score.harness_python() == os.path.join(ROOT, "harness", ".venv", "bin", "python")
