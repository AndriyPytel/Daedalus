"""
The three Acceptance Criteria of the edit runs, computed from sessions copied into `logs/dae529-*/`.

The session list is whatever `*.jsonl` files sit under those directories, one task per file-name prefix up to the first dot, the last session of a task counting. With no session there the measurement is skipped; with sessions it needs `EDIT_TASKS` tasks, the 11 edit tasks of the ticket.
"""
import glob
import json
import os

import pytest

from core.events import APPLIED, DESIGN, PLAN_REVISED, PREFLIGHT_FAILED, replay
from core.loop.execute import _changed_addresses

LOGS = os.path.join(os.path.dirname(__file__), "..", "..", "logs")

EDIT_TASKS = 11

REWORK_SHARE = 0.20


def sessions(logs=LOGS):
    last = {}
    for path in sorted(glob.glob(os.path.join(logs, "dae529-*", "**", "*.jsonl"), recursive=True)):
        last[os.path.basename(path).split(".")[0]] = path
    return [last[task] for task in sorted(last)]


def wall(events):
    return events[-1].at - events[0].at if events else 0.0


def rework(events):
    """Wall after the first plan revision."""
    revised = next((event.at for event in events if event.kind == PLAN_REVISED), None)
    return events[-1].at - revised if revised is not None else 0.0


def rework_share(paths):
    """AC1: rework over wall, summed over the sessions."""
    runs = [replay(path)[0] for path in paths]
    total = sum(wall(events) for events in runs)
    return sum(rework(events) for events in runs) / total if total else 0.0


def op_mismatches(paths):
    """AC2: every Step that failed Preflight with op_mismatch."""
    return [(path, event.data.get("step")) for path in paths for event in replay(path)[0]
            if event.kind == PREFLIGHT_FAILED and event.data.get("gate") == "op_mismatch"]


def outside_the_changed_features(paths):
    """AC3: applied Steps that removed or moved a base-part face outside the features Design marks `changed`."""
    found = []
    for path in paths:
        events = replay(path)[0]
        designs = [event for event in events if event.kind == DESIGN]
        allowed = _changed_addresses((designs[-1].data.get("model") or {}) if designs else {})
        found += [(path, event.data.get("step"), sorted(set(event.data["faces_changed"]) - allowed))
                  for event in events
                  if event.kind == APPLIED and set(event.data.get("faces_changed") or ()) - allowed]
    return found


def test_the_edit_runs_after_the_change_meet_the_three_acceptance_criteria():
    paths = sessions()
    if not paths:
        pytest.skip("no session under logs/dae529-*/ yet")
    assert len(paths) >= EDIT_TASKS

    assert rework_share(paths) <= REWORK_SHARE
    assert op_mismatches(paths) == []
    assert outside_the_changed_features(paths) == []


def _session(directory, name, events):
    path = directory / "dae529-x" / "opus" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"kind": kind, "at": at, "data": data})
                              for kind, at, data in events))
    return str(path)


def test_with_no_session_there_is_nothing_to_measure(tmp_path):
    assert sessions(str(tmp_path)) == []


def test_the_session_list_is_the_files_one_per_task_the_last_one_counting(tmp_path):
    first = _session(tmp_path, "241.20261012-100000.jsonl", [])
    last = _session(tmp_path, "241.20261012-110000.jsonl", [])
    other = _session(tmp_path, "245.20261012-100000.jsonl", [])

    assert sessions(str(tmp_path)) == [last, other] and first not in sessions(str(tmp_path))


def test_rework_is_the_wall_after_the_first_plan_revision(tmp_path):
    path = _session(tmp_path, "241.a.jsonl", [("plan", 0, {})]
                    + [(PLAN_REVISED, 60, {}), (PLAN_REVISED, 70, {}), ("review", 100, {})])

    assert rework_share([path]) == pytest.approx(0.4)


def test_a_run_never_revised_has_no_rework(tmp_path):
    path = _session(tmp_path, "241.a.jsonl", [("plan", 0, {}), ("review", 100, {})])

    assert rework_share([path]) == 0.0


def test_an_op_mismatch_is_counted_and_another_gate_is_not(tmp_path):
    path = _session(tmp_path, "241.a.jsonl", [
        (PREFLIGHT_FAILED, 1, {"step": 3, "gate": "op_mismatch"}),
        (PREFLIGHT_FAILED, 2, {"step": 4, "gate": "collateral"})])

    assert op_mismatches([path]) == [(path, 3)]


def test_an_applied_step_outside_the_changed_features_is_found(tmp_path):
    design = {"features": [{"id": "bore @ plane z = 0", "edit": "changed"}]}
    path = _session(tmp_path, "241.a.jsonl", [
        (DESIGN, 1, {"model": design}),
        (APPLIED, 2, {"step": 1, "faces_changed": ["plane z = 0"]}),
        (APPLIED, 3, {"step": 2, "faces_changed": ["plane z = 0", "plane z = 9"]}),
        (APPLIED, 4, {"step": 3, "faces_changed": []})])

    assert outside_the_changed_features([path]) == [(path, 2, ["plane z = 9"])]
