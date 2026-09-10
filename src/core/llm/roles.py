"""Role client wrapper."""


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
