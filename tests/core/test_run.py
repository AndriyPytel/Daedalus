"""
Level 1 -- `tools/run.py`'s model seam: the provider named on the command line
is the one the client is built for.

`run.py` is a fresh process with no panel behind it, so nothing has fetched a
model list and `core.model_registry` is empty; without the provider being
named, every model resolves to nothing at all.
"""
import os
import sys

from core.llm import ClaudeCodeClient
from core import settings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_provider_is_dialled(monkeypatch):
    from core import model_catalog

    monkeypatch.setattr(model_catalog, "fetch", lambda *args, **kwargs: [])


def test_a_gateway_model_builds_that_gateways_client(the_settings_never_reach_the_user_s_freecad):
    settings.instance().set_api_keys([{"provider": "OpenCode Go", "api_key": "sk-zen"}])

    client = run.client_for("OpenCode Go", "glm-5.3-flash", "medium")

    assert client.model == "openai/glm-5.3-flash"
    assert client.api_base == "https://opencode.ai/zen/go/v1"
    assert client.extra_headers["x-opencode-session"]


def test_a_run_knows_the_output_ceiling_its_provider_lists(
        the_settings_never_reach_the_user_s_freecad, monkeypatch):
    from core import model_catalog

    settings.instance().set_base_urls({"Groq": "https://api.groq.com/openai/v1"})
    monkeypatch.setattr(model_catalog, "fetch", lambda *args, **kwargs: [
        {"id": "qwen/qwen3.8-27b", "display": "Qwen", "max_output": 16384}])

    client = run.client_for("Groq", "qwen/qwen3.8-27b", "medium")

    assert client.output_ceiling == 16384


def test_the_subscription_is_still_answered_by_its_own_client(
        the_settings_never_reach_the_user_s_freecad):
    assert isinstance(run.client_for("Claude (subscription)", "opus", "medium"),
                      ClaudeCodeClient)


class _FakeClient:
    def __init__(self, solid):
        self.solid = solid
        self.exported = []
        self.saved = []

    def has_solid(self):
        return self.solid

    def export_step(self, path):
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
    assert client.exported == [path]


def test_a_passing_run_with_a_solid_exports_it_and_exits_0(tmp_path):
    client = _FakeClient(solid=True)
    path = str(tmp_path / "made.step")

    assert run.finish(client, _Result(ok=True), path) == 0
    assert client.exported == [path]


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
