"""Budget management for the loop: ceilings and revision tracking."""
from dataclasses import dataclass



class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    """
    What a run may spend, and the ceilings that make it terminate. Two
    separate ceilings: mechanical causes (UNCOVERED, MISCOUNTED) spend
    their own budget; execution causes (OBSOLETE, UNBUILDABLE, ASSERTED,
    REJECTED, AMENDED) spend `revisions_ceiling`. Measured: 456 sessions,
    43 blocked on revisions, UNCOVERED appeared 1-3 times, and
    `trailing_gap_retries` bounds the free continuation for a reply that
    stops -- 23 such replies across 13 sessions, 57% within the last two
    Steps, up to 266s call duration.
    """

    attempts_per_step: int = 3
    review_cycles: int = 2
    revisions: int = 3
    mechanical_revisions: int = 2
    rereads: int = 3
    trailing_gap_retries: int = 1


def revisions_ceiling(budgets, steps) -> int:
    """
    How many replans this Plan is allowed, from how long it is.

    A four-Step Plan and a forty-Step Plan cannot have the same allowance: a
    long Plan has more places to hand back from, and every Execute -> Plan
    handback spends one. Still a single ceiling over all seven causes, for the
    reason recorded on `Budgets`; only its size is computed.
    """
    return max(budgets.revisions, len(steps) // 5)


class _Revisions:
    """How many times the Plan has been rewritten, and why."""

    UNCOVERED = "uncovered"
    MISCOUNTED = "miscounted"
    OP_REFUSED = "op_refused"
    AMENDED = "amended"
    OBSOLETE = "obsolete"
    UNBUILDABLE = "unbuildable"
    ASSERTED = "asserted"
    REJECTED = "rejected"

    MECHANICAL_CAUSES = {UNCOVERED, MISCOUNTED, OP_REFUSED}

    _SAID = (
        (UNCOVERED, "the Plan left part of the description unplanned %s"),
        (MISCOUNTED, "the Plan's own arithmetic did not come out %s"),
        (OP_REFUSED, "a Step named an operation off the closed list %s"),
        (AMENDED, "you amended the Plan %s"),
        (OBSOLETE, "Execute found the Plan out of date %s"),
        (UNBUILDABLE, "Execute could not build a Step %s"),
        (ASSERTED, "the acceptance checks failed %s"),
        (REJECTED, "Review rejected the result %s"),
    )

    def __init__(self):
        self._spent = {cause: 0 for cause, _ in self._SAID}

    def spend(self, cause):
        self._spent[cause] += 1

    def spent_total(self) -> int:
        return sum(self._spent.values())

    def mechanical_spent(self) -> int:
        return sum(self._spent[cause] for cause in self.MECHANICAL_CAUSES)

    def execution_spent(self) -> int:
        return sum(self._spent[cause] for cause in self._spent if cause not in self.MECHANICAL_CAUSES)

    def named(self, cause) -> str:
        """The cause in words, without the count `breakdown` puts on it."""
        return (dict(self._SAID)[cause] % "").strip()

    def exhausted(self, budgets, steps, cause=None) -> bool:
        if cause in self.MECHANICAL_CAUSES:
            return self.mechanical_spent() >= budgets.mechanical_revisions
        if cause is not None and cause not in self.MECHANICAL_CAUSES:
            return self.execution_spent() >= revisions_ceiling(budgets, steps)
        return (self.mechanical_spent() >= budgets.mechanical_revisions or
                self.execution_spent() >= revisions_ceiling(budgets, steps))

    def _causes_spent(self) -> list:
        return [cause for cause, _ in self._SAID if self._spent[cause]]

    def breakdown(self) -> str:
        """What the budget actually went on, in the order the causes are listed."""
        said = [text % ("%d time%s" % (self._spent[cause], "" if self._spent[cause] == 1 else "s"))
                for cause, text in self._SAID if self._spent[cause]]
        if not said:
            return "nothing"
        if len(said) == 1:
            return said[0]
        return ", ".join(said[:-1]) + " and " + said[-1]

    def block_reason(self, budgets, steps, cause: str, refused: str) -> str:
        """
        Why the run stopped, and what the budget went on. A ceiling of
        nought is a run that may not replan at all, and it has no
        breakdown to give; where one cause spent the lot the breakdown has
        already named it, so the refusal is not repeated after it. Names
        the ceiling that was hit: mechanical_revisions when a mechanical
        cause blocked, revisions_ceiling when an execution cause blocked.
        """
        if not self._causes_spent():
            return "The Plan may not be rewritten in this run. " + refused

        if cause in self.MECHANICAL_CAUSES:
            ceiling_hit = budgets.mechanical_revisions
            ceiling_name = "the mechanical ceiling"
        else:
            ceiling_hit = revisions_ceiling(budgets, steps)
            ceiling_name = "the whole budget"

        reason = ("The Plan has been rewritten %d times, which is %s — %s."
                  % (ceiling_hit, ceiling_name, self.breakdown()))
        if self._causes_spent() != [cause]:
            reason += " " + refused
        if self._spent[self.OBSOLETE] or cause == self.OBSOLETE:
            reason += (" Execute finding the Plan out of date means something about this "
                       "Document does not match what was asked for.")
        if self._spent[self.UNBUILDABLE] or cause == self.UNBUILDABLE:
            reason += (" A Step Execute cannot build is a Step Plan has to write "
                       "differently; the tracebacks are in what was tried.")
        return reason
