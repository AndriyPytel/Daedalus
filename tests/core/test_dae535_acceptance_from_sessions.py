"""
The Acceptance Criterion of the ticket, computed from sessions copied into `logs/dae535-*/`.

The session list is every `*.jsonl` file under those directories. With no session there yet the measurement is skipped and passes nothing; with sessions, none may hold a Review tool result that fails on `intent` or `outcome`, or a Review reason that says the files are missing.
"""
import glob
import json
import os
import re

import pytest

from core.events import REVIEW_VERDICT, TOOL_RESULT, replay

LOGS = os.path.join(os.path.dirname(__file__), "..", "..", "logs")

REVIEW_PHASES = ("review", "review_suspect")

BLIND = re.compile(r"FileNotFoundError|NameError: name '(intent|outcome)'|(intent|outcome)\.json")


def sessions(logs=LOGS):
    return sorted(glob.glob(os.path.join(logs, "dae535-*", "**", "*.jsonl"), recursive=True))


def blind_reviews(paths):
    """Every Review tool result or verdict reason in *paths* that says `intent` or `outcome` could not be read."""
    found = []
    for path in paths:
        for event in replay(path)[0]:
            if event.kind == TOOL_RESULT and event.data.get("name") in REVIEW_PHASES:
                said = (event.data.get("result") or {}).get("traceback") or ""
            elif event.kind == REVIEW_VERDICT:
                said = event.data.get("reason") or ""
            else:
                continue
            if BLIND.search(said):
                found.append((path, event.kind))
    return found


def test_no_review_after_the_change_is_blind_to_intent_or_outcome():
    paths = sessions()
    if not paths:
        pytest.skip("no session under logs/dae535-*/ yet")

    assert blind_reviews(paths) == []


def _session(directory, name, events):
    path = directory / "dae535-x" / "opus" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"kind": kind, "at": at, "data": data})
                              for kind, at, data in events))
    return str(path)


def test_with_no_session_there_is_nothing_to_measure(tmp_path):
    assert sessions(str(tmp_path)) == []


def test_a_review_tool_result_that_cannot_find_a_name_is_blind(tmp_path):
    path = _session(tmp_path, "208.a.jsonl", [
        (TOOL_RESULT, 1, {"name": "review", "result": {"traceback": "NameError: name 'outcome' is not defined"}}),
        (TOOL_RESULT, 2, {"name": "review", "result": {"traceback": "FileNotFoundError: outcome.json"}}),
        (TOOL_RESULT, 3, {"name": "execute", "result": {"traceback": "NameError: name 'outcome' is not defined"}}),
        (TOOL_RESULT, 4, {"name": "review", "result": {"stdout": "['solids']"}})])

    assert blind_reviews([path]) == [(path, TOOL_RESULT)] * 2


def test_a_verdict_that_says_the_files_are_missing_is_blind(tmp_path):
    path = _session(tmp_path, "208.a.jsonl", [
        (REVIEW_VERDICT, 1, {"verdict": "fail", "reason": "outcome.json and intent.json are not in the working directory"}),
        (REVIEW_VERDICT, 2, {"verdict": "pass", "reason": "the bore is centred"})])

    assert blind_reviews([path]) == [(path, REVIEW_VERDICT)]
