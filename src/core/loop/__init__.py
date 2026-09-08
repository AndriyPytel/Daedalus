"""
The Design -> Plan -> Execute -> Assertion -> Review loop.

This package was split from a single loop.py file. The public API is:
- run: the main entry point
- Budgets, revisions_ceiling: budget configuration
- LoopResult: return type of run()
- Approval, APPROVE, ACCEPT, etc.: gate constants
- Plan, Checked, etc.: data types

Import from the submodules for internal usage.
"""
from core.loop.run import run
from core.loop.budgets import Budgets, revisions_ceiling
from core.loop.results import LoopResult, Progress, Stopped
from core.loop.gate import (
    Approval, APPROVE, ACCEPT, AMEND, REJECT, REVISE, 
    START_OVER, DECIDE_FOR_ME, DECISIONS,
    auto_approve, revise_by_default, nobody_to_ask, NeedsAnswer,
)
from core.loop.prompt import (
    DESIGN_PHASE, PLAN_PHASE, EXECUTE_PHASE, ASSERTION_PHASE,
    REVIEW_PHASE, REVIEW_SUSPECT_PHASE, PROMPTS,
)
from core.loop.plan import Plan
from core.loop.execute import Written, ExecuteOutcome, Looks
from core.loop.assertion import Checked
