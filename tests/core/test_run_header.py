"""
Level 1 -- the first event of every `tools/run.py` log says how the run was made,
so a log alone reproduces it.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402


def test_the_header_names_arm_model_effort_turns_and_commit():
    args = run.parser().parse_args(["--intent", "x", "--arm", "agent", "--model", "opus",
                                    "--effort", "medium", "--max-turns", "12"])

    header = run.header(args)

    assert header["arm"] == "agent" and header["model"] == "opus"
    assert header["effort"] == "medium" and header["max_turns"] == 12
    assert re.fullmatch(r"[0-9a-f]{40}", header["commit"])
    assert header["provider"] == "Claude (subscription)"


def test_the_arm_defaults_to_the_pipeline_and_rejects_anything_else():
    parsed = run.parser().parse_args(["--intent", "x"])

    assert parsed.arm == "pipeline" and parsed.max_turns == 60
    try:
        run.parser().parse_args(["--intent", "x", "--arm", "bogus"])
    except SystemExit as exit_:
        assert exit_.code == 2
    else:
        raise AssertionError("an unknown arm was accepted")


def test_the_header_is_the_first_line_of_the_log_and_replay_carries_it(tmp_path):
    from core.events import EventStream, INTENT, replay

    log = str(tmp_path / "run.jsonl")
    args = run.parser().parse_args(["--intent", "x", "--arm", "oneshot", "--model", "opus"])
    run.write_header(log, args)
    stream = EventStream()
    stream.log_to(log)
    stream.emit(INTENT, text="x")

    events, truncated = replay(log)

    assert [e.kind for e in events] == ["run", INTENT] and not truncated
    assert events[0].data["arm"] == "oneshot"
