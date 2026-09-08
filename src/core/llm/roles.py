"""Profile and role client wrappers."""
from core.llm.effort_policy import profile_of


class ProfiledClient:
    """
    The client `core/loop.py :: _one_call` finds `for_phase` on.

    It carries no phase of its own -- `thinking` and `doing` do, each already
    built for the model and the effort its profile is set to -- and it is
    never asked to stream anything itself; `for_phase` hands back the client
    that will.
    """

    def __init__(self, thinking, doing):
        self.thinking = thinking
        self.doing = doing

    def for_phase(self, phase: str):
        return self.thinking if profile_of(phase) == "thinking" else self.doing


class RoleClient:
    """
    The client a run is actually made with: one built per role the table
    names, wired so `for_phase` answers a phase from the row that names it
    (FCC-312), replacing the two-profile split `client_from_profiles` drew.

    A role the table has never heard of -- no phase, the orchestrator or a
    sub-agent worker call this module has no site for yet, since the table
    itself carries every role the epic named -- answers from `caller`, the
    client the caller of `for_phase`/`for_role` was already running under,
    not a cheaper default: the epic's settled fallback rule.
    """

    def __init__(self, roles: dict, caller):
        self.roles = roles
        self.caller = caller

    def for_role(self, role: str):
        return self.roles.get(role, self.caller)

    def for_phase(self, phase: str):
        return self.for_role(phase)
