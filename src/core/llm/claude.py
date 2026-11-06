"""
ClaudeCodeClient: streams from a Claude Max subscription through one
`claude` process a call. Every CLI flag is a measurement, not a taste --
`--tools ""` disabling every tool cuts the preamble 29,213 to
2,385 tokens, and `--safe-mode` (not `--bare`) keeps subscription auth from
being forced onto ANTHROPIC_API_KEY. Timeout follows `--effort`:
52s/429s/668s measured at low/medium/high, and `_CEILING` is twice each,
rounded up to five minutes.
"""
import collections
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Iterator

from core.llm.types import REASONING, TEXT, Chunk
from core.llm.exceptions import ProviderRefused
from core.llm.effort_policy import _CEILING, _resolved_effort
from core.llm.claude_stream import (
    _system_of,
    _user_turn,
    _parsed,
    _refusal_in,
    _withheld_thinking,
    _said,
    _tokens_of,
)

_WATCH_SECONDS = 1.0


class Run:
    """
    The live `claude` processes one run's calls have spawned.

    One per `ClaudeCodeClient`, so `kill` walking a role table or a
    thinking/doing profile ends the processes of that client tree alone --
    where `core/llm.py`'s global `_live` could only ever kill every run at
    once, which is the whole of Stop and Abort's problem.
    """

    def __init__(self):
        self._live = set()
        self._lock = threading.Lock()

    def register(self, process):
        with self._lock:
            self._live.add(process)

    def release(self, process):
        with self._lock:
            self._live.discard(process)

    def end(self):
        """Kill every process this run currently has in flight."""
        with self._lock:
            live = list(self._live)
        for process in live:
            process.kill()


def kill(client):
    """
    End every live `claude` process reachable from *client*'s own tree.

    `RoleClient` and `ProfiledClient` (`core/llm/__init__.py`) name no process
    of their own -- the process belongs to whichever `ClaudeCodeClient` a role
    or profile resolves to, and a fan-out (`ThreadPoolExecutor`) may hold
    several of those live under the one `Run` at once. *client* is `None`
    where a Session has not built one yet, which is a no-op rather than an
    error: there is nothing running to kill.
    """
    if client is None:
        return
    if isinstance(client, ClaudeCodeClient):
        client.run.end()
        return
    roles = getattr(client, "roles", None)
    if roles is not None:
        for role_client in roles.values():
            kill(role_client)
        kill(getattr(client, "caller", None))
        return
    for profile in ("thinking", "doing"):
        nested = getattr(client, profile, None)
        if nested is not None:
            kill(nested)


_CLAUDE_CANDIDATES = (
    os.path.expanduser("~/.local/bin/claude"),
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
)


def find_claude() -> str:
    """
    Locate the `claude` CLI, or raise saying how to point at it.

    The binary installs to `~/.local/bin/claude`, and
    `env -i PATH=/usr/bin:/bin sh -c 'command -v claude'` finds nothing — which
    is the PATH a Finder-launched FreeCAD inherits. The same three steps as
    `core/preflight.py :: find_freecadcmd`, for the same reason.
    """
    explicit = os.environ.get("CLAUDE_CMD")
    if explicit:
        return explicit
    for path in _CLAUDE_CANDIDATES:
        if os.path.exists(path):
            return path
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError(
        "No claude CLI found. Set CLAUDE_CMD to the path of the claude binary, or "
        "install it and sign in with `claude auth login`."
    )


def signed_in(run: Callable = None) -> tuple:
    """
    Who the CLI is signed in as, as (it worked, one sentence): the Test
    button's proof for a provider with no key. Every other provider's Test
    is the model list fetched with the key; here there is no key and the
    model list is three aliases, so what is left to prove is the account
    and the plan, and `claude auth status` answers that spending no call
    at all. `authMethod` is in the sentence because a CLI holding an API
    key answers this too, and a run on one is billed by the token under a
    label that says subscription.
    """
    if run is None:
        run = subprocess.run
    try:
        answered = run([find_claude(), "auth", "status", "--json"],
                       capture_output=True, text=True, timeout=30)
    except Exception as why:
        return False, str(why)

    try:
        status = json.loads(answered.stdout)
    except ValueError:
        return False, (answered.stderr or answered.stdout or "").strip()[:200]

    if not status.get("loggedIn"):
        return False, "The claude CLI is not signed in. Run `claude auth login`."
    return True, "Signed in as %s through %s, on a %s subscription." % (
        status.get("email", "?"), status.get("authMethod", "?"),
        status.get("subscriptionType", "?"))


class ClaudeCodeClient:
    """
    Streams from a Claude Max subscription through one `claude` process a
    call. Every CLI flag is a measurement, not a taste -- `--tools ""`
    disabling every tool cuts the preamble 29,213 to 2,385 tokens, and
    `--safe-mode` (not `--bare`) keeps subscription auth from being forced
    onto ANTHROPIC_API_KEY. Timeout follows `--effort`: 52s/429s/668s
    measured at low/medium/high, and `_CEILING` is twice each, rounded up
    to five minutes.
    """

    def __init__(self, model: str, spawn: Callable = None,
                 timeout: float = None, effort: str = None, run: Run = None):
        self.model = model
        self.effort = _resolved_effort(effort)
        self.timeout = _CEILING[self.effort] if timeout is None else timeout
        self.last_cost = None
        self._spawn = spawn
        self.run = run if run is not None else Run()

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        """
        Stream a reply to *messages*, chunk by chunk. The system prompt
        goes as a file because Windows caps a whole command line at
        32,767 characters and `config/prompts/design.md` is more than
        that on its own, where `ARG_MAX` on macOS is 1,048,576, so argv
        survives here and not there and FreeCAD ships to both. The process
        starts in one fixed empty directory, because the CLI writes its
        working directory into the prompt and a new one per call rewrites
        the whole cache; `overrides` are LiteLLM's per-call knobs the CLI
        takes none of.
        """
        spawn = self._spawn or _spawn_claude
        self.last_cost = None
        workspace = tempfile.mkdtemp(prefix="fcc-claude-")
        cwd = os.path.join(tempfile.gettempdir(), "fcc-claude")
        os.makedirs(cwd, exist_ok=True)
        try:
            system = os.path.join(workspace, "system.md")
            with open(system, "w", encoding="utf-8") as handle:
                handle.write(_system_of(messages))
            for chunk in self._answered(
                    spawn(_invocation(self.model, system, self.effort), cwd),
                    messages):
                yield chunk
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _answered(self, process, messages) -> Iterator[Chunk]:
        """
        One process's reply, under a ceiling read off the wall clock, since the monotonic clock stops while the machine sleeps and a timer on it never fired across an overnight sleep.

        Constraint: killed on timeout since a silent `claude` gives nothing to
        check time against; thinking reported once per call, not per delta.
        Measured: 1506 per-delta chunks once buried a session in an unusable count.
        """
        overdue = threading.Event()

        finished = threading.Event()
        deadline = time.time() + self.timeout

        def watch():
            while not finished.wait(_WATCH_SECONDS):
                if time.time() >= deadline:
                    overdue.set()
                    process.kill()
                    return

        threading.Thread(target=watch, daemon=True).start()
        self.run.register(process)
        try:
            process.stdin.write(json.dumps(_user_turn(messages)) + "\n")
            process.stdin.flush()
            process.stdin.close()

            said_it_was_thinking = False
            noise = collections.deque(maxlen=20)
            for line in process.stdout:
                event = _parsed(line)
                if event is None:
                    if line.strip():
                        noise.append(line.strip())
                    continue
                if event.get("total_cost_usd") is not None:
                    self.last_cost = {"usd": event["total_cost_usd"], "model": self.model}
                    answered = next(iter(event.get("modelUsage") or {}), None)
                    if answered:
                        self.last_cost["resolved_model"] = answered
                    self.last_cost.update(_tokens_of(event.get("usage") or {}))
                refusal = _refusal_in(event, self.model)
                if refusal is not None:
                    if self.last_cost is None and event.get("stop_reason") == "max_tokens":
                        self.last_cost = {"model": self.model, "usage_unavailable": True}
                    raise refusal
                if _withheld_thinking(event) and not said_it_was_thinking:
                    said_it_was_thinking = True
                    yield Chunk(REASONING, "")
                for chunk in _said(event):
                    yield chunk

            code = process.wait()
            if overdue.is_set():
                raise ProviderRefused(
                    "%s refused the request: the provider did not answer in time. Try "
                    "again, or ask for a smaller Step." % self.model,
                    "no reply in %g seconds" % self.timeout)
            if code:
                detail = "\n".join(noise).strip()
                if not detail:
                    detail = ((process.stderr and process.stderr.read()) or "").strip()
                raise ProviderRefused(
                    "%s could not be reached: the claude CLI exited with status %s. "
                    "Check it is installed and signed in with `claude auth status`."
                    % (self.model, code), detail[:400])
        finally:
            finished.set()
            self.run.release(process)
            process.kill()


def _spawn_claude(argv: list, cwd: str):
    """
    One `claude` per call, with its own pipes and no key in its
    environment. The pipes are decoded as UTF-8 rather than by the locale,
    because inside FreeCAD.app neither LANG nor LC_ALL is set, so the
    locale resolves to ascii and the first non-ASCII byte kills the read.
    stderr is merged into stdout, since `claude` runs with `--verbose` and
    an undrained stderr pipe fills its OS buffer and deadlocks the write,
    taking the stdout read loop down with it; `_parsed` already drops any
    line that is not the expected JSON, so the merged noise is skipped
    rather than mistaken for an event.
    """
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def _environment() -> dict:
    """
    What the process is started with: this one, less the key.

    `ANTHROPIC_API_KEY` in the shell is what the CLI reaches for first, and a
    run that reaches for it is billed by the token under a label that says
    subscription.
    """
    return {name: value for name, value in os.environ.items()
            if name != "ANTHROPIC_API_KEY"}


def _invocation(model: str, system_prompt: str, effort: str) -> list:
    """
    The command line one call is made with, in the order it was measured.
    *effort* is the client's own resolved dial, because on this route it
    is the one thing that changes the answer and the one the panel offers,
    and a run split into profiles sends each phase's own client's effort
    here, not one dial shared by both. `--include-partial-messages` is
    what makes it a stream at all: without it the CLI answers with the
    finished `assistant` message and the `result`, no `content_block_delta`
    arrives, and a phase reading deltas gets an empty reply from a call
    that succeeded and was paid for.
    """
    return [
        find_claude(), "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", model,
        "--system-prompt-file", system_prompt,
        "--effort", effort,
        "--no-session-persistence",
        "--safe-mode",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--disable-slash-commands",
        "--tools", "",
    ]
