"""
`tools/report.py`'s pure-Python markdown build, over synthetic jsonl logs --
no FreeCAD needed.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import report  # noqa: E402


def _write(path, events):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for at, kind, data in events:
            handle.write(json.dumps({"kind": kind, "at": at, "data": data}) + "\n")


def test_a_log_with_no_events_is_marked_failed_to_start(tmp_path):
    _write(str(tmp_path / "haiku" / "mm25.jsonl"), [])

    text = report.report(str(tmp_path))

    assert "failed to start" in text


def test_a_log_that_cannot_be_parsed_is_also_failed_to_start(tmp_path):
    path = tmp_path / "haiku" / "mm25.jsonl"
    os.makedirs(path.parent)
    path.write_text("{not json\n")

    text = report.report(str(tmp_path))

    assert "failed to start" in text


def test_assertion_counts_are_summed_across_events(tmp_path):
    _write(str(tmp_path / "haiku" / "mm25.jsonl"), [
        (1, "assertion", {"confirmed": ["a", "b"], "unmet": ["c"]}),
        (2, "assertion", {"confirmed": ["d"], "unmet": []}),
        (3, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = report.report(str(tmp_path))

    assert "3/1" in text


def test_models_are_sorted_by_pass_rate_descending(tmp_path):
    _write(str(tmp_path / "weak" / "mm25.jsonl"), [
        (1, "review_verdict", {"verdict": "fail", "reason": "no"}),
    ])
    _write(str(tmp_path / "strong" / "mm25.jsonl"), [
        (1, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = report.report(str(tmp_path))
    summary = text.split("## Summary")[1].split("## Detail")[0]

    assert summary.index("strong") < summary.index("weak")


def test_no_logs_reports_that_plainly(tmp_path):
    text = report.report(str(tmp_path))

    assert "no sweep logs found" in text


def test_question_count_is_summed_in_summary_and_shown_in_detail(tmp_path):
    _write(str(tmp_path / "haiku" / "mm25.jsonl"), [
        (1, "question", {"text": "Which hole pattern?"}),
        (2, "answer", {"text": "4x M6"}),
        (3, "question", {"text": "Material?"}),
        (4, "answer", {"text": "6061-T6"}),
        (5, "review_verdict", {"verdict": "pass", "reason": "ok"}),
    ])

    text = report.report(str(tmp_path))
    summary = text.split("## Summary")[1].split("## Detail")[0]
    detail = text.split("## Detail")[1]

    assert "2.0" in summary
    assert "questions" in detail
