"""
Level 1 -- the role-to-model table every run is built from.

`RoleClient` is the seam `core/loop.py :: _one_call` asks `for_phase` of: a
phase is answered by the row that names it, and a role with no row of its own
by the caller.
"""
from core.llm import ROLES, RoleClient, ScriptedLLMClient


def test_every_role_the_epic_named_is_in_the_table():
    assert set(ROLES) == {
        "orchestrator", "design", "plan", "execute", "assertion", "review",
        "sub-agent worker",
    }


def test_for_phase_answers_from_the_row_that_names_the_phase():
    design = ScriptedLLMClient(["design reply"])
    execute = ScriptedLLMClient(["execute reply"])
    caller = ScriptedLLMClient(["caller reply"])
    client = RoleClient(roles={"design": design, "execute": execute}, caller=caller)

    assert client.for_phase("design") is design
    assert client.for_phase("execute") is execute


def test_for_role_answers_the_orchestrator_and_a_sub_agent_worker_too():
    orchestrator = ScriptedLLMClient(["orchestrator reply"])
    worker = ScriptedLLMClient(["worker reply"])
    caller = ScriptedLLMClient(["caller reply"])
    client = RoleClient(
        roles={"orchestrator": orchestrator, "sub-agent worker": worker},
        caller=caller)

    assert client.for_role("orchestrator") is orchestrator
    assert client.for_role("sub-agent worker") is worker


def test_a_role_not_in_the_table_falls_back_to_the_caller_not_a_cheaper_default():
    caller = ScriptedLLMClient(["caller reply"])
    client = RoleClient(roles={"design": ScriptedLLMClient(["design reply"])}, caller=caller)

    assert client.for_role("execute") is caller
    assert client.for_phase("execute") is caller


def test_removing_a_roles_row_falls_the_phase_back_to_the_caller():
    caller = ScriptedLLMClient(["caller reply"])
    design = ScriptedLLMClient(["design reply"])
    client = RoleClient(roles={"design": design}, caller=caller)

    assert client.for_phase("design") is design

    client.roles.pop("design")

    assert client.for_phase("design") is caller


def test_a_role_whose_model_no_provider_reports_falls_back_to_the_caller(monkeypatch):
    """
    The default table (`core.role_defaults`) names Anthropic models. A user on
    the subscription provider has never chosen those, and their first Intent
    used to die on the first row instead of running on the model they did
    choose.
    """
    import types

    from core.llm import factory

    def build(model_id=None, effort=None):
        if model_id == "claude-sonnet-5":
            raise ValueError("'claude-sonnet-5' is not a model any configured "
                             "provider reports.")
        return ScriptedLLMClient([model_id or "caller"])

    monkeypatch.setattr(factory, "client_from_settings", build)
    monkeypatch.setattr(factory.settings, "instance", lambda: types.SimpleNamespace(
        get_role_table=lambda: [{"role": "design", "model": "claude-sonnet-5"},
                                {"role": "execute", "model": "opus"}]))

    client = factory.client_from_roles()

    assert client.for_role("design") is client.caller
    assert client.for_role("execute") is not client.caller
