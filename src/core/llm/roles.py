"""Role client wrapper."""
import copy

from core.llm.effort_policy import _CEILING


class RoleClient:
    """
    The client a run is actually made with: one built per role the table
    names. A role the table has never heard of answers from `caller`.
    """

    def __init__(self, roles: dict, caller):
        self.roles = roles
        self.caller = caller

    def for_role(self, role: str):
        return self.roles.get(role, self.caller)

    def for_phase(self, phase: str):
        return self.for_role(phase)

    def __copy__(self):
        """
        A shallow copy of the table alone leaves every role pointing at the
        same client instance, which is exactly what a `ThreadPoolExecutor`
        fan-out asks `copy.copy` to avoid. Each role's client is shallow-copied
        too, so a per-call attribute like `last_cost` lands on a copy no other
        thread holds.
        """
        return RoleClient(
            roles={role: copy.copy(client) for role, client in self.roles.items()},
            caller=copy.copy(self.caller),
        )


def patient_for(llm, role):
    """*llm*, or a copy of it whose client for *role* waits as long as an xhigh call may take, when that client has a ceiling to raise."""
    client = llm.for_role(role) if isinstance(llm, RoleClient) else llm
    if not hasattr(client, "timeout") or client.timeout >= _CEILING["xhigh"]:
        return llm
    patient = copy.copy(llm)
    (patient.for_role(role) if isinstance(patient, RoleClient) else patient).timeout = _CEILING["xhigh"]
    return patient
