"""
Trap verification: a throwaway kernel proves or disproves what a proposed
Trap claims, before it ever reaches the user (FCC-314).

A `Trap` with a `script` is run once in a fresh `core.kernel.Kernel`: the
script's own assertion decides pass or fail, and either way the kernel's
`KernelResult` is the evidence attached to the proposal -- a failed
reproduction is shown as a failed reproduction, never dropped or shown as
confirmed. A `Trap` with no script cannot be reduced to a runnable check and
is marked unverified rather than skipped, which is why `unverified` is a
result and not the absence of one.

The 15 seed traps in `core/freecad/freecad_tools.py:TRAPS` are prose, carried over
rather than rewritten into scripts here (per this ticket's Solution Notes),
so `verify_seed_traps` is their first exercise through this mechanism, not a
claim that they reproduce -- each one comes back unverified until a script
is written for it.
"""
from dataclasses import dataclass

from core.kernel import Kernel

VERIFIED_PASS = "verified-pass"
VERIFIED_FAIL = "verified-fail"
UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Trap:
    claim: str
    script: str = None


@dataclass(frozen=True)
class TrapEvidence:
    """What the user sees for a proposed Trap: the claim, the label, and the
    kernel's own result where there is one to show."""

    trap: Trap
    status: str
    result: object = None


def verify_trap(trap, kernel_factory=Kernel):
    """
    Run *trap*'s script in a throwaway kernel and label the outcome.

    A missing script is not run at all -- it is `unverified`, the third
    label the Acceptance Criteria name alongside pass and fail, so a claim
    that cannot be reduced to a runnable check still reaches the user
    instead of being silently dropped.
    """
    if not trap.script:
        return TrapEvidence(trap=trap, status=UNVERIFIED)
    with kernel_factory() as kernel:
        result = kernel.check(trap.script, document=None)
    status = VERIFIED_PASS if result.ok else VERIFIED_FAIL
    return TrapEvidence(trap=trap, status=status, result=result)


def verify_seed_traps():
    """Run verification against every seed trap in `core.freecad.freecad_tools.TRAPS`."""
    from core.freecad.freecad_tools import TRAPS
    return [verify_trap(Trap(claim=text)) for text in TRAPS]
