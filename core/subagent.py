"""
One tree-wide supervisor bounding sub-agent delegation to depth 1, under
turn, token and concurrency budgets tracked across the whole spawn tree --
not per child. Wall time is recorded, never enforced: implied throughput
across the recorded runs behind this ticket spans 485-2,877 tokens/second by
model and load, so a clock limit binds the machine and the provider rather
than the work (epic FCC-291 Problem Statement).

A phase agent (Design, Plan, Execute, Assertion, Review, the orchestrator)
is depth 0; a worker it spawns is depth 1; depth 1's own spawn attempt is
refused outright, never retried -- the fixed phase chain handing off between
phases is not a spawn and never touches this tree at all. A refusal is
appended to `refusals` rather than silently dropped or looped on, so a
caller (FCC-308's event wiring) can turn it into a `SUB_AGENT_REFUSED`
event; the `KINDS` themselves are FCC-308's to add, not this module's.

TURN_BUDGET (30) is the p90 of provider calls observed over 72 real runs (46
of this repo's own session logs plus 26 ab-rlm-agentic benchmark runs).
TOKEN_BUDGET (2_600_000) is the p90 of 54 priced runs' `cost` events,
inverted against litellm's own price table under an assumed 50/50
input/output split -- provisional per the ticket's own OPEN QUESTIONS, to be
re-derived once FCC-309 records real token counts.
"""
import math
import os

from core.events import SUB_AGENT_ANSWERED, SUB_AGENT_REFUSED, SUB_AGENT_SPAWNED
from core.llm import TEXT, Session

TURN_BUDGET = 30
TOKEN_BUDGET = 2_600_000
MB_PER_KERNEL = 100

BUDGET_EXHAUSTED = "budget-exhausted"


def _free_ram_mb():
    """
    Best-effort free RAM in MB. `SC_AVPHYS_PAGES` is POSIX-only and absent on
    some platforms; an unmeasurable host errs generous, because the
    concurrency knee a production sweep found sits at the core count, not at
    the RAM ceiling (8 kernels/14 cores: +0.7%; 16: +71%; 24: +128%) -- RAM
    never binds a realistic machine and cores always do.
    """
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        avail_pages = os.sysconf("SC_AVPHYS_PAGES")
        return (page_size * avail_pages) // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 8192


def default_concurrency(cpu_count=None, free_ram_mb=None):
    """`min(cores, floor(free_ram_mb / 100))` -- the machine's own answer, not a constant."""
    cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    free_ram_mb = free_ram_mb if free_ram_mb is not None else _free_ram_mb()
    return max(1, min(cpu_count, math.floor(free_ram_mb / MB_PER_KERNEL)))


class SessionTreeSupervisor:
    """
    One supervisor per run, holding the whole spawn tree's counters.

    `spawn(depth)` is the only gate: it returns the child's depth
    (`depth + 1`) when delegation is allowed, and `None` -- with the refusal
    appended to `refusals` -- when depth, a budget, or concurrency forbids
    it. `release()` frees the concurrency slot a returning worker held.
    """

    def __init__(self, turn_budget=TURN_BUDGET, token_budget=TOKEN_BUDGET, concurrency=None):
        self.turn_budget = turn_budget
        self.token_budget = token_budget
        self.concurrency = concurrency if concurrency is not None else default_concurrency()
        self.turns = 0
        self.tokens = 0
        self.wall_time = 0.0
        self.active_kernels = 0
        self.refusals = []

    @property
    def outcome(self):
        if self.turns > self.turn_budget or self.tokens > self.token_budget:
            return BUDGET_EXHAUSTED
        return None

    def spawn(self, depth):
        if depth >= 1:
            self.refusals.append({"reason": "depth", "depth": depth})
            return None
        if self.outcome == BUDGET_EXHAUSTED:
            self.refusals.append({"reason": self.outcome, "depth": depth})
            return None
        if self.active_kernels >= self.concurrency:
            self.refusals.append(
                {"reason": "concurrency", "depth": depth, "limit": self.concurrency})
            return None
        self.active_kernels += 1
        return depth + 1

    def release(self):
        self.active_kernels = max(0, self.active_kernels - 1)

    def record_turn(self):
        self.turns += 1

    def record_tokens(self, count):
        self.tokens += count

    def record_wall_time(self, seconds):
        """Telemetry only -- accumulated, never checked by `outcome` or `spawn`."""
        self.wall_time += seconds


def delegate(supervisor, phase_session, phase_depth, llm, prompt, stream=None, phase=None):
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
    still going, not only once it answers (FCC-308) -- and either
    `SUB_AGENT_ANSWERED` (carrying the worker's full transcript, since only
    `answer` reaches *phase_session*) or `SUB_AGENT_REFUSED` follows.
    """
    child_depth = supervisor.spawn(phase_depth)
    if child_depth is None:
        if stream is not None:
            stream.emit(SUB_AGENT_REFUSED, phase=phase, reason=supervisor.refusals[-1]["reason"])
        return None
    if stream is not None:
        stream.emit(SUB_AGENT_SPAWNED, phase=phase, prompt=prompt)
    worker_session = Session()
    try:
        supervisor.record_turn()
        answer = "".join(
            chunk.text for chunk in worker_session.turn(llm, prompt) if chunk.kind == TEXT)
    finally:
        supervisor.release()
    phase_session.messages.append({"role": "assistant", "content": answer})
    if stream is not None:
        stream.emit(SUB_AGENT_ANSWERED, phase=phase, answer=answer,
                    transcript=list(worker_session.messages))
    return answer
