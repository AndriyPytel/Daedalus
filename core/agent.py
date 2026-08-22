"""
The agent: a bounded multi-turn loop over one phase call, run inside FreeCAD's
own interpreter rather than a separate kernel process (FCC-183).

`_ask` (`core/loop.py`) is one provider call: system and user go out, and
whatever comes back is parsed as the phase's JSON object or the phase failed.
That is right for a phase that only needs to answer, and wrong for one that
needs to *find out* first — Plan asking the drawing was already answered by a
Tool call at the caller's level (`_look`, `core/loop.py`), one whole `_ask`
apart from the turn that used the answer. `ask` here folds that into a single
conversation: between the first user turn and the phase's final object, the
model may spend a turn on an action instead — `{"look": {"tool": ..., "arguments":
{...}}}` to call a read-only Tool, or `{"run": "..."}` to execute Python in a
namespace that survives across the whole call — and is asked again with the
result appended, the same `prior=` mechanism `_streamed` already carries so
`system` and `user` are billed once for the whole conversation, not once per
turn.

The return contract is `_ask`'s own: the phase's parsed JSON object, or a
`PhaseError`. Nothing downstream of a phase call can tell whether it went
through `_ask` or through here — that is the whole of the seam in
`core/loop.py :: plan`, which is off by default.

No kernel, no subprocess, no ipykernel: `Namespace.run` executes in the
calling process, the way Execute's own code already runs against the Document
(`core/apply.py`). A `PhaseError` raised by that code is the one exception
`Namespace.run` does not turn into traceback text — it is the model's own
code saying the phase cannot continue, and it reaches `ask`'s caller exactly
as `_ask` would have raised it, never rewrapped.
"""
import json
import traceback
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from core.events import TOOL_CALL, TOOL_RESULT
from core.tools import ToolError

_NOTHING_SAID = "(nothing was said)"


class Namespace:
    """
    The names one phase call's actions share, and the code that runs them.

    A plain dict, not a sandbox: the model already writes the code Execute
    applies to a live Document (`core/apply.py`), so a second, more limited
    Python is not a wall against anything a `run` action could otherwise
    reach through a Tool. What it buys is persistence — a name bound by one
    action is still bound by the next, in the same process, without paying a
    kernel's own startup or a socket to talk to it across.

    `PhaseError` rides in the namespace from construction so a `run` action
    can raise it by name, the same way it is raised anywhere else in this
    codebase, and have it mean the same thing here: the phase cannot go on,
    not a traceback to read and try again from.
    """

    def __init__(self):
        from core.loop import PhaseError

        self._names = {"PhaseError": PhaseError}

    def run(self, code: str) -> str:
        """
        Execute *code* against the persistent names, and hand back everything
        it printed, wrote to stderr, or failed with, as one piece of text.

        Routed through `core.apply.on_main_thread` first: this is the one
        place a `run` action could reach a live Document, through a name an
        earlier action bound, and a Document mutation off the Qt main thread
        is how FreeCAD dies. Where there is no Qt to marshal onto at all —
        `RuntimeError` for no application, `ImportError` for no binding,
        which is every test in this suite and `freecadcmd` alike — the code
        runs directly instead, the same fallback `utils/logger.py` already
        takes for the identical reason.
        """
        from core.apply import on_main_thread
        from core.loop import PhaseError

        buffer = StringIO()

        def execute():
            with redirect_stdout(buffer), redirect_stderr(buffer):
                exec(compile(code, "<agent>", "exec"), self._names)  # noqa: S102

        try:
            try:
                on_main_thread(execute)
            except (RuntimeError, ImportError):
                execute()
        except PhaseError:
            raise
        except BaseException:
            buffer.write(traceback.format_exc())
        return buffer.getvalue()


def ask(llm, events, phase, system, user, tools, budgets, namespace=None):
    """
    One phase's turn, the way `_ask` gives it, but able to spend bounded turns
    finding things out first.

    `budgets` is required rather than defaulted here: `Budgets()` already
    holds a considered default for `agent_turns` (`core/loop.py`), and a
    second, silent one in this function would only hide a caller that forgot
    to think about it. A caller that means the default says so, with
    `Budgets()` of its own.

    `namespace` is optional so that one call still gets a fresh `Namespace`
    the way it always has. A caller that holds several calls of one phase
    invocation together — Assertion across its retries (FCC-189) — passes the
    same `Namespace` into each, so a name a `run` action bound on one call is
    still bound on the next: the persistence FCC-183 built stays inside one
    call otherwise, and a phase that measures the same result more than once
    a run would have no way to keep what the first measurement already
    worked out.

    `budgets.agent_turns` bounds the whole conversation, this call's own turn
    and every action it takes together — without it a model that keeps
    looking or keeps running code instead of answering never terminates.
    Exhausting it is a `PhaseError` naming the action the model was still
    partway through, since that is what a person reading the transcript has
    to fix: the phase never got to answer, and knowing what it was doing
    when it ran out is the only way to tell a slow phase from a stuck one.

    `budgets.looks_per_turn` is the same ceiling Plan and Execute already
    spend a look against (`core/loop.py`), read here rather than duplicated:
    a look through the agent is still a look, and a Tool call taken through
    an action and one taken through the caller-level `_look` loop draw on one
    allowance, not two.

    A reply carrying no object is asked again once, in the middle of one turn
    of this conversation rather than instead of it — exactly what `_ask`
    already does and why (`core/loop.py :: _ask`), because a phase does not
    get to become more brittle for having become an agent. The retry is not
    a turn of its own: it costs a second provider call, the way it already
    does for `_ask`, and `last_action` still names whichever action this turn
    was on when it started.

    A `run` action's own `PhaseError` is not caught here either — it is
    `Namespace.run`'s to let through, and this loop's job is only to not get
    in its way.
    """
    from core.loop import PhaseError, _as_json, _asked_again, _object_of, _streamed

    namespace = namespace if namespace is not None else Namespace()
    prior = []
    looks_taken = 0
    last_action = "sent its first turn"

    for _ in range(budgets.agent_turns):
        body, thought, reasoning = _streamed(llm, events, phase, system, user, prior=prior)
        if not isinstance(_object_of(body), dict):
            body, thought, reasoning = _streamed(
                llm, events, phase, system, user,
                prior=prior + _asked_again(body, reasoning))
        if thought and not body.strip():
            raise PhaseError(
                "%s streamed its reasoning and then stopped without answering, mid "
                "conversation. Send the Intent again, or pick a model that thinks less "
                "before it commits." % phase
            )
        reply = _as_json(body, phase)

        look = reply.get("look")
        if isinstance(look, dict) and look:
            last_action = "looking with %r" % str(look.get("tool") or "")
            result, looks_taken = _take_a_look(tools, look, events, looks_taken, budgets)
            prior = prior + _turn_of(body, reasoning, result)
            continue

        run = reply.get("run")
        if isinstance(run, str) and run.strip():
            last_action = "running code"
            result = _run_code(namespace, run, events)
            prior = prior + _turn_of(body, reasoning, result)
            continue

        return reply

    raise PhaseError(
        "%s spent all %d of its turns without answering; it was last %s."
        % (phase, budgets.agent_turns, last_action)
    )


def _take_a_look(tools, look, events, looks_taken, budgets):
    """One `look` action: a read-only Tool call, exactly as `ToolRegistry.call`
    answers it, or the error that stands in for it. Returns the result to feed
    back and the look count, bumped only where the budget allowed the Tool to
    actually be called — a look refused for want of budget is not a second
    thing this turn spent it on.

    A Tool `_lookable` would not offer (`core/loop.py`) is refused the same
    way `_look` refuses it: `apply_step` writes to the Document, `review_views`
    moves the user's camera, and `check_assertions` belongs to a phase of its
    own, so none of the three is a look a `run` action could not do more
    plainly by naming what it wants."""
    from core.loop import NOT_A_LOOK

    name = str(look.get("tool") or "")
    arguments = dict(look.get("arguments") or {})
    events.emit(TOOL_CALL, name=name, arguments=arguments)

    if looks_taken >= budgets.looks_per_turn:
        events.emit(TOOL_RESULT, name=name, result={"error": "budget spent"})
        return ({"error": "the look budget for this turn is spent. Answer with your "
                          "object, or run code with what you already know."}, looks_taken)

    if name in NOT_A_LOOK or name not in tools:
        result = {"error": "%r is not a Tool you can look with. Reply with code instead."
                           % name}
    else:
        try:
            result = tools.call(name, arguments)
        except ToolError as exc:
            result = {"error": str(exc)}

    events.emit(TOOL_RESULT, name=name, result=result)
    return result, looks_taken + 1


def _run_code(namespace, code, events):
    """One `run` action: whatever the code printed, wrote to stderr, or
    failed with, as the text a `run` action's result carries."""
    events.emit(TOOL_CALL, name="run", arguments={"code": code})
    output = namespace.run(code)
    events.emit(TOOL_RESULT, name="run", result={"output": output})
    return {"output": output}


def _turn_of(body, reasoning, result):
    """The model's own reply, as its assistant turn, then the action's result
    as the next user turn — `_asked_again`'s own shape (`core/loop.py`), so a
    turn spent on an action costs the wire what a turn spent re-asking does
    and no more."""
    said = (body or reasoning or "").strip() or _NOTHING_SAID
    return [{"role": "assistant", "content": said},
            {"role": "user", "content": json.dumps(result, indent=2, default=str)}]
