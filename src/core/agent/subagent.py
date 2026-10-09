"""One tree-wide supervisor bounding sub-agent delegation under
turn and token budgets tracked across the whole spawn tree.

TURN_BUDGET (30) is the p90 of provider calls over 72 real runs, when every code turn counted;
now only a turn that builds a Step or fixes one does.
TOKEN_BUDGET (2_600_000) is the p90 of 54 priced runs' `cost` events.
"""
from core.events import SUB_AGENT_ANSWERED, SUB_AGENT_REFUSED, SUB_AGENT_SPAWNED
from core.llm import TEXT, Session

TURN_BUDGET = 30
TOKEN_BUDGET = 2_600_000

BUDGET_EXHAUSTED = "budget-exhausted"

STEP_TURNS = 3
"""Turns every Step is owed out of the run's pool, whatever the Steps before it spent."""

RESERVE = 3
"""Turns held back before `turn_budget`'s wall: room for one forced
declaration and the one retry that follows a refused Step, so `run_phase`
can still ask for a choice while a turn remains to answer it, rather than
only once `outcome` has already fired."""


class SessionTreeSupervisor:
    """
    One supervisor per run, holding the whole spawn tree's counters.

    `spawn()` is the only gate: it is true when delegation is allowed, and
    false -- with the refusal appended to `refusals` -- when the budget forbids it.
    """

    def __init__(self, turn_budget=TURN_BUDGET, token_budget=TOKEN_BUDGET):
        self.turn_budget = turn_budget
        self.token_budget = token_budget
        self.turns = 0
        self.tokens = 0
        self.refusals = []
        self._exhaustion_told = False
        self.step_budget = None
        self.step_turns = 0
        self._step_exhaustion_told = False

    @property
    def run_outcome(self):
        if self.turns > self.turn_budget or self.tokens > self.token_budget:
            return BUDGET_EXHAUSTED
        return None

    @property
    def outcome(self):
        if self.run_outcome or (self.step_budget is not None
                                and self.step_turns > self.step_budget):
            return BUDGET_EXHAUSTED
        return None

    def begin_step(self, remaining):
        """
        Give the Step now being written its own turns: twice its fair share of
        what the pool still holds, but never so many that each of the
        `remaining - 1` Steps after it has fewer than `STEP_TURNS` left.
        Called again as each Step lands, so what it did not spend goes back.
        """
        left = self.turn_budget - self.turns - RESERVE
        others = max(remaining - 1, 0)
        fair = left // max(remaining, 1)
        self.step_budget = max(STEP_TURNS, min(2 * fair, left - STEP_TURNS * others))
        self.step_turns = 0
        self._step_exhaustion_told = False

    def end_step(self):
        self.step_budget = None
        self.step_turns = 0

    def reserved(self):
        """
        True once fewer than `RESERVE` turns remain before `turn_budget`,
        while `outcome` is still `None` -- the point `run_phase` treats as
        the reserve crossing, ahead of the hard ceiling `outcome` itself
        answers for. A `turn_budget` no bigger than `RESERVE` itself has no
        room to reserve anything ahead of `outcome`, so it never fires.
        """
        if self.outcome is not None or self.turn_budget <= RESERVE:
            return False
        return self.turns >= self.turn_budget - RESERVE

    def exhaustion_reason(self):
        """
        The one-time transition into `BUDGET_EXHAUSTED`, told once for this
        supervisor's whole life -- a run threads one supervisor through
        every phase continuation, so its ceiling firing is one event, not
        one per continuation that asks after it already has.
        """
        if self.run_outcome == BUDGET_EXHAUSTED and not self._exhaustion_told:
            self._exhaustion_told = True
            return {"turns": self.turns, "budget": self.turn_budget, "scope": "run"}
        if self.run_outcome is None and self.outcome == BUDGET_EXHAUSTED \
                and not self._step_exhaustion_told:
            self._step_exhaustion_told = True
            return {"turns": self.step_turns, "budget": self.step_budget, "scope": "step"}
        return None

    def spawn(self):
        if self.run_outcome == BUDGET_EXHAUSTED:
            self.refusals.append({"reason": self.outcome})
            return False
        return True

    def record_turn(self):
        self.turns += 1
        self.step_turns += 1

    def record_tokens(self, count):
        self.tokens += count


def delegate(supervisor, phase_session, llm, prompt, stream=None, phase=None):
    """
    Run one delegated turn under *supervisor*, or refuse it.

    The worker gets its own `Session`; only its answer is appended to
    *phase_session*, as the worker's single turn -- its own conversation
    dies with it once this returns, which is the return contract the epic
    settles: "A sub-agent returns an answer, not a transcript." Returns
    `None` without touching *phase_session* or *llm* when `supervisor.spawn`
    refuses the delegation.

    *stream* is the run's `EventStream` and *phase* the spawning phase's
    name; when given, `SUB_AGENT_SPAWNED` fires before the worker's own turn
    runs -- so a delegating phase renders as a live row while the worker is
    still going, not only once it answers -- and either
    `SUB_AGENT_ANSWERED` (carrying the worker's full transcript, since only
    `answer` reaches *phase_session*) or `SUB_AGENT_REFUSED` follows.
    """
    if not supervisor.spawn():
        if stream is not None:
            stream.emit(SUB_AGENT_REFUSED, phase=phase, reason=supervisor.refusals[-1]["reason"])
        return None
    if stream is not None:
        stream.emit(SUB_AGENT_SPAWNED, phase=phase, prompt=prompt)
    worker_session = Session()
    supervisor.record_turn()
    answer = "".join(
        chunk.text for chunk in worker_session.turn(llm, prompt) if chunk.kind == TEXT)
    spent = getattr(llm, "last_cost", None) or {}
    supervisor.record_tokens(spent.get("prompt_tokens", 0) + spent.get("completion_tokens", 0))
    phase_session.messages.append({"role": "assistant", "content": answer})
    if stream is not None:
        stream.emit(SUB_AGENT_ANSWERED, phase=phase, answer=answer,
                    transcript=list(worker_session.messages))
    return answer
