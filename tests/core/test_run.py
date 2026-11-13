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
