"""
Level 1 — none of the checks that made a Step wrong got lost when
`core/preflight_worker.py` became `core/kernel_worker.py`.

`git show pre-rlm-rewrite-dev-baseline:core/preflight_worker.py :: preflight()`
raised on exactly five things, in this order: a recompute that left an object
`Invalid`, a feature that moved no material, a Body in more than one piece,
a Body with an invalid Shape, and a Body with an inverted solid.
`core/kernel_worker.py :: _run_step_body` is the direct descendant of that
function, so this pins each check by the name of the function that runs it
there, read out of the compiled function's own `co_names` rather than out of
its source text -- a docstring or a raise message can be reworded without
this test caring, but a check call deleted from the body fails it.

No sixth check: the mixed-concavity Body check's own history shows a
console-warning-capture mechanism was investigated and rejected as
non-viable before this check -- `FreeCAD.Console.AddObserver` does not exist
in `freecadcmd`, and redirecting fds around a headless recompute captures
nothing, because `Result has multiple solids. Only keeping the first.` is a
GUI report-view message FreeCAD never writes when there is no GUI, per a
comment dated 2026-08-02. There is no console message this kernel could
capture, so there is nothing here for a sixth check to run.
"""
from core.kernel import kernel_worker

OLD_CHECKS = {
    "a recompute left an object Invalid": "_objects_left_in_error",
    "a feature moved no material": "_features_that_moved_no_material",
    "a Body left in more than one piece": "_bodies_in_pieces",
    "a Body with an invalid Shape": "_bodies_with_invalid_shape",
    "a Body with an inverted solid": "_bodies_with_inverted_solid",
}


def test_every_old_preflight_check_still_exists_as_a_function():
    missing = [name for name in OLD_CHECKS.values() if not hasattr(kernel_worker, name)]
    assert not missing, "check function(s) deleted from core/kernel_worker.py: %s" % missing


def test_every_old_preflight_check_still_runs_after_a_step():
    called = set(kernel_worker._run_step_body.__code__.co_names)
    dropped = {label: name for label, name in OLD_CHECKS.items() if name not in called}
    assert not dropped, "check(s) no longer called from _run_step_body: %s" % dropped
