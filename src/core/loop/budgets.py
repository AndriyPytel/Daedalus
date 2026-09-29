"""Budget management for the loop: ceilings and revision tracking."""
from dataclasses import dataclass



class PhaseError(Exception):
    """A phase gave back something the loop cannot act on."""


@dataclass
class Budgets:
    """
    What a run may spend, and the ceilings that make it terminate. Two
    separate ceilings: mechanical causes (UNCOVERED, VALUE_UNDEFINED) spend
    their own whole-run budget; execution causes (OBSOLETE, UNBUILDABLE,
    ASSERTED, REJECTED, AMENDED) spend at most `revisions` replans against
    the Step that caused them, and `revisions_ceiling` overall -- a
    19-Step Plan whose three replans all went on one wedged Step left 14
    Steps standing before the run died, six fillets never attempted.
    `trailing_gap_retries` bounds the free continuation for a reply that
    stops -- 23 such replies across 13 sessions, 57% within the last two
    Steps, up to 266s call duration; `readings` repeats a Design page-sized
    group's own reading that many times, merged the same grow-only way,
    because a dimension digit at the provider's pixel cap is a stochastic
    read; `record_conflicts` false drops the disagreement's record at the
    Gate while the held reading still stands, isolating what recording a
    conflict itself contributes when the rest of a run, `readings`
    included, is held the same.
    """

    attempts_per_step: int = 3
    review_cycles: int = 2
    revisions: int = 3
    mechanical_revisions: int = 2
    rereads: int = 3
    settles: int = 2
    readings: int = 3
    trailing_gap_retries: int = 1
    record_conflicts: bool = True


def revisions_ceiling(budgets, steps) -> int:
    """
    The whole-run backstop on execution replans, over every Step combined.

    Each Step is already capped at `budgets.revisions` replans of its own
    (`_Revisions.exhausted`); the smallest backstop that never binds
    before every Step has spent that ceiling is one Step's worth of
    replans times how many Steps there are. Anything smaller would have
    blocked the recorded run this replaced, where one Step spent all 3 of
    its 3 replans and the other 18 Steps of that 19-Step Plan still
    needed room to spend their own.
    """
    return len(steps) * budgets.revisions


class _Revisions:
    """How many times the Plan has been rewritten, and why -- in total, and per Step."""

    UNCOVERED = "uncovered"
    VALUE_UNDEFINED = "value_undefined"
    OP_REFUSED = "op_refused"
    AMENDED = "amended"
    OBSOLETE = "obsolete"
    UNBUILDABLE = "unbuildable"
    ASSERTED = "asserted"
    REJECTED = "rejected"

    MECHANICAL_CAUSES = {UNCOVERED, VALUE_UNDEFINED, OP_REFUSED}

    _SAID = (
        (UNCOVERED, "the Plan left part of the description unplanned %s"),
        (VALUE_UNDEFINED, "a Step named a value the Plan never computed %s"),
        (OP_REFUSED, "a Step named an operation off the closed list %s"),
        (AMENDED, "you amended the Plan %s"),
        (OBSOLETE, "Execute found the Plan out of date %s"),
        (UNBUILDABLE, "Execute could not build a Step %s"),
        (ASSERTED, "the acceptance checks failed %s"),
        (REJECTED, "Review rejected the result %s"),
    )

    def __init__(self):
        self._spent = {cause: 0 for cause, _ in self._SAID}
        self._step_spent = {}

    def spend(self, cause, step=None):
        self._spent[cause] += 1
        if step is not None:
            self._step_spent[step] = self._step_spent.get(step, 0) + 1

    def spent_total(self) -> int:
        return sum(self._spent.values())

    def mechanical_spent(self) -> int:
        return sum(self._spent[cause] for cause in self.MECHANICAL_CAUSES)

    def execution_spent(self) -> int:
        return sum(self._spent[cause] for cause in self._spent if cause not in self.MECHANICAL_CAUSES)

    def step_spent(self, step) -> int:
        return self._step_spent.get(step, 0)

    def named(self, cause) -> str:
        """The cause in words, without the count `breakdown` puts on it."""
        return (dict(self._SAID)[cause] % "").strip()

    def exhausted(self, budgets, steps, cause=None, step=None) -> bool:
        if cause in self.MECHANICAL_CAUSES:
            return self.mechanical_spent() >= budgets.mechanical_revisions
        if cause is not None and cause not in self.MECHANICAL_CAUSES:
            if step is not None and self.step_spent(step) >= budgets.revisions:
                return True
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

    def _what_landed(self, steps, applied) -> str:
        """
        What the Document holds when the run stops, and what it never tried.

        `steps` is the latest revision's own continuation, not the whole
        Plan -- an applied Step is carried in History rather than restated,
        so the total this run ever had is what already landed plus what
        the latest revision still lists.
        """
        total = applied + len(steps)
        if not steps:
            return " All %d Steps are in the Document." % applied if applied else ""
        return (" %d of %d Steps are in the Document; Steps %d-%d were never attempted."
               % (applied, total, applied + 1, total))

    def block_reason(self, budgets, steps, cause: str, refused: str, applied: int,
                     step=None) -> str:
        """
        Why the run stopped, and what the budget went on. A ceiling of
        nought is a run that may not replan at all, and it has no
        breakdown to give; where one cause spent the lot the breakdown has
        already named it, so the refusal is not repeated after it. Names
        the ceiling that was hit -- mechanical_revisions for a mechanical
        cause, a Step's own `revisions` for an execution cause that wedged
        one Step, `revisions_ceiling` for the whole-run backstop -- and
        always says which Steps landed and which never ran.
        """
        if not self._causes_spent():
            return "The Plan may not be rewritten in this run. " + refused + self._what_landed(steps, applied)

        if cause in self.MECHANICAL_CAUSES:
            ceiling_hit = budgets.mechanical_revisions
            ceiling_name = "the mechanical ceiling"
        elif step is not None and self.step_spent(step) >= budgets.revisions:
            ceiling_hit = budgets.revisions
            ceiling_name = "Step %d's own ceiling" % (step + 1)
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
        return reason + self._what_landed(steps, applied)
