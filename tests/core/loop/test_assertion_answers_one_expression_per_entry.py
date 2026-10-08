"""Assertion asks the model for one expression per entry, evaluates each alone, and retries only the entries whose expression raised."""
import json

from core.events import ASSERTION, EventStream
from core.kernel.kernel_worker import check_each
from core.llm import ScriptedLLMClient
from core.loop import assertion as assertion_module
from core.loop.assertion import assertion
from core.tools import ToolRegistry

_ENTRIES = ["features \u2192 hole-a \u2192 position \u2192 the left end face: 20 mm",
            "features \u2192 hole-a \u2192 position \u2192 the front face: 15 mm",
            "features \u2192 hole-a \u2192 depth: 12 mm",
            "features \u2192 hole-a \u2192 direction: outward"]


def _tools(tmp_path):
    scripts = []
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})

    def check(code):
        if "check_each" not in code:
            return {"ok": True, "geometry": {}}
        scripts.append(code)
        namespace = {"d": 20.0}
        namespace["check_each"] = check_each(namespace)
        exec(code, namespace)
        return {"ok": True, "held": namespace["held"], "failed": namespace["failed"],
                "defects": namespace["defects"], "geometry": {}}

    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(check)
    return registry, scripts


def _model(monkeypatch, *replies):
    asked = []
    remaining = list(replies)

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        asked.append(json.loads(task))
        return remaining.pop(0), None, []

    monkeypatch.setattr(assertion_module, "run_phase", fake_run_phase)
    return asked


def _confirmed(checked):
    return [entry for entry in checked.confirmed if entry in _ENTRIES]


def _answer(*items):
    return {"checks": list(items)}


def _run(tmp_path, entries=_ENTRIES):
    tools, scripts = _tools(tmp_path)
    events = EventStream()
    return assertion(entries, {}, tools, ScriptedLLMClient([]), events), scripts, events


def test_the_model_is_sent_every_entry_with_its_number_and_answers_with_expressions(monkeypatch, tmp_path):
    asked = _model(monkeypatch, _answer(*({"entry": n, "expr": "d == 20.0"} for n in range(4))))

    checked, scripts, _ = _run(tmp_path)

    assert asked[0]["acceptance"] == [{"entry": n, "text": text} for n, text in enumerate(_ENTRIES)]
    assert _confirmed(checked) == _ENTRIES and not checked.failed and checked.defects == []
    assert len(scripts) == 1


def test_an_expression_that_is_false_fails_its_entry_and_no_other(monkeypatch, tmp_path):
    _model(monkeypatch, _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 1, "expr": "d == 15.0"},
                                {"entry": 2, "expr": "d == 20.0"}, {"entry": 3, "expr": "d > 0"}))

    checked, _, events = _run(tmp_path)

    assert checked.failed and checked.unmet == [_ENTRIES[1]]
    assert _confirmed(checked) == [_ENTRIES[0], _ENTRIES[2], _ENTRIES[3]]
    assert events.first(ASSERTION).data["ok"] is False


def test_an_expression_that_raises_is_a_defect_of_that_entry_and_the_others_keep_their_result(monkeypatch, tmp_path):
    _model(monkeypatch,
           _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 1, "expr": "no_such_name > 1"},
                   {"entry": 2, "expr": "d == 99"}, {"entry": 3, "expr": "d > 0"}),
           _answer({"entry": 1, "expr": "no_such_name > 1"}),
           _answer({"entry": 1, "expr": "no_such_name > 1"}))

    checked, _, _ = _run(tmp_path)

    assert checked.defects == [_ENTRIES[1]]
    assert _ENTRIES[1] not in _confirmed(checked) and _ENTRIES[1] not in checked.unmet
    assert _ENTRIES[1] in checked.uncovered
    assert _confirmed(checked) == [_ENTRIES[0], _ENTRIES[3]]
    assert checked.unmet == [_ENTRIES[2]]


def test_an_expression_that_gives_neither_true_nor_false_is_a_defect(monkeypatch, tmp_path):
    reply = _answer(*({"entry": n, "expr": "d" if n == 0 else "d == 20.0"} for n in range(4)))
    _model(monkeypatch, reply, _answer({"entry": 0, "expr": "d"}), _answer({"entry": 0, "expr": "d"}))

    checked, _, _ = _run(tmp_path)

    assert checked.defects == [_ENTRIES[0]] and _ENTRIES[0] not in _confirmed(checked)


def test_a_retry_sends_back_only_the_entries_that_raised_with_the_error(monkeypatch, tmp_path):
    asked = _model(monkeypatch,
                   _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 1, "expr": "oops"},
                           {"entry": 2, "expr": "d == 99"}, {"entry": 3, "expr": "nope"}),
                   _answer({"entry": 1, "expr": "d == 20.0"}, {"entry": 3, "expr": "d > 0"}))

    checked, scripts, _ = _run(tmp_path)

    assert [item["entry"] for item in asked[1]["acceptance"]] == [1, 3]
    raised = asked[1]["your_checks_that_raised"]
    assert [(item["entry"], item["expr"]) for item in raised] == [(1, "oops"), (3, "nope")]
    assert "NameError" in raised[0]["error"]
    assert len(scripts) == 2
    assert "d == 20.0" in scripts[1] and "d == 99" not in scripts[1]
    assert _confirmed(checked) == [_ENTRIES[0], _ENTRIES[1], _ENTRIES[3]]
    assert checked.unmet == [_ENTRIES[2]] and checked.defects == []


def test_an_entry_that_held_or_failed_is_never_asked_again(monkeypatch, tmp_path):
    asked = _model(monkeypatch,
                   _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 1, "expr": "d == 1"},
                           {"entry": 2, "expr": "oops"}, {"entry": 3, "expr": "d > 0"}),
                   _answer({"entry": 2, "expr": "d == 20.0"}))

    _run(tmp_path)

    assert len(asked) == 2
    assert [item["entry"] for item in asked[1]["acceptance"]] == [2]


def test_an_entry_the_model_gave_up_on_is_uncovered_and_not_asked_again(monkeypatch, tmp_path):
    asked = _model(monkeypatch,
                   _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 1, "uncovered": "no face to measure from"},
                           {"entry": 2, "expr": "d == 20.0"}, {"entry": 3, "expr": "d == 20.0"}))

    checked, _, _ = _run(tmp_path)

    assert len(asked) == 1
    assert checked.uncovered == [_ENTRIES[1]] and _ENTRIES[1] not in _confirmed(checked)
    assert not checked.failed and checked.defects == []


def test_an_entry_with_no_answer_is_asked_again_and_stays_unconfirmed_if_never_answered(monkeypatch, tmp_path):
    answer = _answer({"entry": 0, "expr": "d == 20.0"}, {"entry": 2, "expr": "d == 20.0"},
                     {"entry": 3, "expr": "d == 20.0"})
    asked = _model(monkeypatch, answer, _answer(), _answer())

    checked, _, _ = _run(tmp_path)

    assert [item["entry"] for item in asked[1]["acceptance"]] == [1]
    assert checked.defects == [_ENTRIES[1]] and _ENTRIES[1] not in _confirmed(checked)


def test_a_script_reply_is_not_run(monkeypatch, tmp_path):
    _model(monkeypatch, {"code": "held = []\nfailed = []"}, {"code": "x"}, {"code": "x"})

    checked, scripts, _ = _run(tmp_path)

    assert scripts == []
    assert _confirmed(checked) == [] and sorted(checked.defects) == sorted(_ENTRIES)


def test_the_attempts_are_bounded(monkeypatch, tmp_path):
    from core.loop.budgets import Budgets

    asked = _model(monkeypatch, *[_answer(*({"entry": n, "expr": "oops"} for n in range(4)))] * 5)
    tools, _ = _tools(tmp_path)

    assertion(_ENTRIES, {}, tools, ScriptedLLMClient([]), EventStream(), Budgets())

    assert len(asked) == Budgets().attempts_per_step
