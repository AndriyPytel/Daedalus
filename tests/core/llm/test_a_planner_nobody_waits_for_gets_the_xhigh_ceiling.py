"""Where nobody answers, Plan's calls wait as long as an xhigh call may take, whatever the effort."""
from core.llm.claude import ClaudeCodeClient
from core.llm.effort_policy import _CEILING
from core.llm.roles import RoleClient, patient_for


def test_the_plan_client_of_a_medium_effort_run_gets_the_xhigh_ceiling_on_a_copy():
    planner = ClaudeCodeClient(model="opus", effort="medium")
    other = ClaudeCodeClient(model="opus", effort="medium")
    roles = RoleClient({"plan": planner, "execute": other}, caller=other)

    patient = patient_for(roles, "plan")

    assert patient.for_role("plan").timeout == _CEILING["xhigh"]
    assert patient.for_role("execute").timeout == _CEILING["medium"]
    assert planner.timeout == _CEILING["medium"]
