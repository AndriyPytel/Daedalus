"""
Level 1 — the CLI wrapper around `core.measure`, run without a REPL.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "measure_session.py")
FIXTURES = os.path.join(os.path.dirname(__file__), "recorded", "sessions")


def _fixture(name):
    return os.path.join(FIXTURES, name)


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, check=True)


def test_measuring_one_log_reports_every_field():
    result = _run(_fixture("reached_verdict.jsonl"))

    assert "verdict: pass" in result.stdout
    assert "steps_started: 3" in result.stdout


def test_measuring_two_logs_reports_one_block_each():
    result = _run(_fixture("reached_verdict.jsonl"), _fixture("blocked.jsonl"))

    assert result.stdout.count("verdict:") == 2
    assert "blocked_reason: None" in result.stdout


def test_comparing_prints_the_markdown_table():
    result = _run("--baseline", _fixture("reached_verdict.jsonl"),
                  "--experiment", _fixture("blocked.jsonl"),
                  "--baseline-label", "main", "--experiment-label", "rlm-version")

    assert "| metric | main (n=1) | rlm-version (n=1) |" in result.stdout


def test_no_arguments_prints_help_and_fails():
    result = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True)

    assert result.returncode == 2
    assert "usage" in result.stdout.lower()
