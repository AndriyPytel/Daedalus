"""Level 1 -- `tools/run.py`: what it writes when a run ends, and the arguments it refuses."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402
import pytest  # noqa: E402


class _FakeClient:
    def __init__(self, solid):
        self.solid = solid
        self.exported = []
        self.saved = []

    def has_solid(self):
        return self.solid

    def export_step(self, path):
        open(path, "w").close()
        self.exported.append(path)

    def save(self, path):
        self.saved.append(path)


class _Result:
    def __init__(self, ok):
        self.ok = ok


def test_a_run_with_no_solid_writes_no_step_and_exits_with_its_own_code(tmp_path):
    client = _FakeClient(solid=False)

    code = run.finish(client, _Result(ok=True), str(tmp_path / "made.step"))

    assert client.exported == []
    assert code == run.NO_SOLID_EXIT
    assert code not in (0, 1)


def test_a_failed_run_with_a_solid_still_exports_it_and_exits_1(tmp_path):
    client = _FakeClient(solid=True)
    path = str(tmp_path / "made.step")

    assert run.finish(client, _Result(ok=False), path) == 1
    assert os.path.exists(path)


def test_a_passing_run_with_a_solid_exports_it_and_exits_0(tmp_path):
    client = _FakeClient(solid=True)
    path = str(tmp_path / "made.step")

    assert run.finish(client, _Result(ok=True), path) == 0
    assert os.path.exists(path)


class _FlatClient:
    def __init__(self, path):
        self.path = path

    def export_ifc(self, path):
        return self.path and path


def test_an_ifc_run_with_no_arch_object_writes_nothing_and_exits_with_its_own_code(tmp_path):
    assert run.finish(_FlatClient(None), _Result(ok=True), str(tmp_path / "flat.ifc")) == run.NO_SOLID_EXIT


def test_an_ifc_run_exits_with_its_verdict(tmp_path):
    path = str(tmp_path / "flat.ifc")

    assert run.finish(_FlatClient(path), _Result(ok=True), path) == 0
    assert run.finish(_FlatClient(path), _Result(ok=False), path) == 1


def test_an_effort_the_dial_does_not_offer_is_an_argument_error(monkeypatch):
    import sdk

    monkeypatch.setattr(sdk, "client_for", lambda *args: pytest.fail("a client was built"))
    monkeypatch.setattr(sys, "argv", ["run.py", "--intent", "x", "--effort", "ultra"])

    with pytest.raises(SystemExit) as stopped:
        run.main()

    assert stopped.value.code == 2


def test_help_lists_the_task_and_intent_flags(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run.py", "--help"])

    with pytest.raises(SystemExit) as stopped:
        run.main()

    assert stopped.value.code == 0
    flags = capsys.readouterr().out
    for kept in ("--task", "--intent"):
        assert kept in flags


def test_a_run_with_no_task_and_no_intent_is_an_argument_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run.py", "--model", "x"])

    with pytest.raises(SystemExit) as stopped:
        run.main()

    assert stopped.value.code == 2
    assert "Intent" in capsys.readouterr().err


def test_a_step_is_handed_to_the_record_of_the_run_that_wrote_it(tmp_path):
    client = _FakeClient(solid=True)
    path = str(tmp_path / "made.step")
    recorded = []

    run.finish(client, _Result(ok=True), path, record=recorded.append)

    assert recorded == [path]


def test_a_save_that_is_not_a_step_is_not_recorded(tmp_path):
    recorded = []

    run.finish(_FakeClient(solid=True), _Result(ok=True), str(tmp_path / "made.FCStd"), record=recorded.append)

    assert recorded == []
