"""
Trap verification: a throwaway kernel proves or disproves what a proposed
Trap claims, before it ever reaches the user.
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
    """Run verification against every seed trap in `core.freecad_apply.TRAPS`."""
    from core.freecad_apply import TRAPS
    return [verify_trap(Trap(claim=text)) for text in TRAPS]
