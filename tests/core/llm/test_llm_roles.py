"""
Level 1 -- the role-to-model table wired into `core/llm.py` (FCC-312),
replacing the two static thinking/doing profiles at the production call
site (`ui/copilot_panel.py :: _RunContext._get_client`).

`RoleClient` is the seam `core/loop.py :: _one_call` still asks `for_phase`
of -- the phases keep the interface `ProfiledClient` already gave it, only
the answer for the row is by role now, not by a `thinking`/`doing` split.
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
