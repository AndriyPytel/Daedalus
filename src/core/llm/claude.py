"""
ClaudeCodeClient: streams from a Claude Max subscription through one `claude`
process a call.

Constraints: every CLI flag is a measurement, not a taste — `--disallowed-tools`
naming every reported tool cuts the preamble 29,213 to 2,385 tokens; `--safe-mode`
(not `--bare`) keeps subscription auth from being forced onto ANTHROPIC_API_KEY.
`last_cost` is the one fact besides Chunks that crosses this seam, cleared as each
call starts. Timeout follows `--effort`: 52s/429s/668s measured at low/medium/high;
`_CEILING` is twice each, rounded up to five minutes.
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
from core.llm.effort_policy import EFFORTS, _CEILING, _SUBSCRIPTION_EFFORT


_CLAUDE_CANDIDATES = (
    os.path.expanduser("~/.local/bin/claude"),
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
)

_DISALLOWED_TOOLS = (
    "Task", "Bash", "CronCreate", "CronDelete", "CronList", "DesignSync",
    "Edit", "EnterWorktree", "ExitWorktree", "ListAgents", "Monitor",
    "NotebookEdit", "PushNotification", "Read", "RemoteTrigger",
    "ReportFindings", "ScheduleWakeup", "SendMessage", "Skill", "TaskCreate",
    "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate", "ToolSearch",
    "WebFetch", "WebSearch", "Workflow", "Write",
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
    Who the CLI is signed in as, as (it worked, one sentence).

    The Test button's proof for a provider with no key. Every other provider's
    is the model list fetched with the key (`provider_manager ::
    test_connection`); there is no key here and the model list is three
    aliases, so what is left to prove is the account and the plan — and
    `claude auth status` answers that spending no call at all.

    `authMethod` is in the sentence because a CLI holding an API key answers
    this too, and a run on one is billed by the token under a label that says
    subscription.
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
    """Streams from a Claude Max subscription through one `claude` process a call.

    Constraints: every CLI flag is a measurement, not a taste — `--disallowed-tools` naming every
    reported tool cuts the preamble 29,213 to 2,385 tokens; `--safe-mode` (not `--bare`) keeps
    subscription auth from being forced onto ANTHROPIC_API_KEY.
    `last_cost` is the one fact besides Chunks that crosses this seam, cleared as each call starts.
    Timeout follows `--effort`: 52s/429s/668s measured at low/medium/high; `_CEILING` is twice each,
    rounded up to five minutes.
    """

    def __init__(self, model: str, spawn: Callable = None,
                 timeout: float = None, effort: str = None):
        self.model = model
        self.effort = _resolved_effort(effort)
        self.timeout = _CEILING[self.effort] if timeout is None else timeout
        self.last_cost = None
        self._spawn = spawn

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        """
        Stream a reply to *messages*, chunk by chunk.

        The system prompt goes as a file because Windows caps a whole command
        line at 32,767 characters and `config/prompts/design.md` is more than
        that on its own, where `ARG_MAX` on macOS is 1,048,576: argv survives
        here and not there, and FreeCAD ships to both. The file lives in a
        temporary directory the process is started in, which settles the
        working directory at the same time — no repository's `CLAUDE.md` sits
        beside it.

        `overrides` are LiteLLM's per-call knobs and the CLI takes none of
        them; the signature is the seam's.
        """
        spawn = self._spawn or _spawn_claude
        self.last_cost = None
        workspace = tempfile.mkdtemp(prefix="fcc-claude-")
        try:
            system = os.path.join(workspace, "system.md")
            with open(system, "w", encoding="utf-8") as handle:
                handle.write(_system_of(messages))
            for chunk in self._answered(
                    spawn(_invocation(self.model, system, self.effort), workspace),
                    messages):
                yield chunk
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _answered(self, process, messages) -> Iterator[Chunk]:
        """
        One process's reply, under the wall-clock ceiling.

        The ceiling kills the process rather than watching the clock between
        lines: a `claude` that writes nothing is read with a blocking loop, and
        nothing arrives to check the time against. The kill is what ends that
        loop, and `overdue` is what says the end was ours.

        Thinking whose words the provider withholds is reported once per call,
        as one wordless chunk. That the model reasoned is a fact worth the
        panel's row -- a phase that takes four minutes has to say it is doing
        something -- and it is the only fact this route gives: `sonnet` and
        `opus` stream the thinking as an empty string and a rising token
        count, and the words never follow. One chunk rather than one per
        delta, because the count was reported before and 1506 lines of it
        filled a 45-minute session with a number nobody can act on.

        `noise` keeps the last lines `_parsed` dropped as not-JSON: since
        stderr is merged into stdout (`_spawn_claude`), that is where the CLI's
        plain-text error lands on a failing exit, and it is the only detail
        left to report once `process.stderr` is `None`. `process.stderr` is
        still read when `noise` is empty, for a `process` that keeps it
        separate rather than merged.
        """
        overdue = threading.Event()

        def give_up():
            overdue.set()
            process.kill()

        ceiling = threading.Timer(self.timeout, give_up)
        ceiling.start()
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
                refusal = _refusal_in(event, self.model)
                if refusal is not None:
                    raise refusal
                if _withheld_thinking(event) and not said_it_was_thinking:
                    said_it_was_thinking = True
                    yield Chunk(REASONING, "")
                for chunk in _said(event):
                    yield chunk
                if event.get("total_cost_usd") is not None:
                    self.last_cost = {"usd": event["total_cost_usd"], "model": self.model}
                    self.last_cost.update(_tokens_of(event.get("usage") or {}))

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
            ceiling.cancel()
            process.kill()


def _resolved_effort(explicit: str = None) -> str:
    """*explicit* when it is one `EFFORTS` offers; the stored dial otherwise."""
    if explicit in EFFORTS:
        return explicit
    from core.llm.factory import effort
    return effort()


def _spawn_claude(argv: list, cwd: str):
    """
    One `claude` per call, with its own pipes and no key in its environment.

    The pipes are decoded as UTF-8 rather than by the locale: inside FreeCAD.app
    neither LANG nor LC_ALL is set, so the locale resolves to ascii and the
    first non-ASCII byte kills the read.

    stderr is merged into stdout: `claude` runs with `--verbose`, and an
    undrained stderr pipe fills its OS buffer and deadlocks the write, taking
    the stdout read loop down with it. `_parsed` already drops any line that
    is not the expected JSON, so the merged CLI noise is skipped rather than
    mistaken for an event.
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

    *effort* is the client's own resolved dial, because on this route it is
    the one thing that changes the answer and the one the panel offers: the
    low end exists against a model circling invisibly in its own reasoning,
    and here the circling streams and the call is bounded. A run split into
    profiles sends each phase's own client's effort here, not one dial shared
    by both.

    `--include-partial-messages` is what makes it a stream at all: without it
    the CLI answers with the finished `assistant` message and the `result`, no
    `content_block_delta` arrives, and a phase reading deltas gets an empty
    reply from a call that succeeded and was paid for.
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
        "--disallowed-tools", ",".join(_DISALLOWED_TOOLS),
    ]


def _system_of(messages: list) -> str:
    """Every system message, as the prompt `--system-prompt-file` replaces the CLI's with."""
    return "\n\n".join(_as_text(message.get("content"))
                       for message in messages if message.get("role") == "system")


def _as_text(content) -> str:
    """A message's text, whether it travels as a string or as blocks."""
    if isinstance(content, str):
        return content
    return "\n\n".join(piece.get("text", "") for piece in content or ()
                       if piece.get("type") == "text")


def _user_turn(messages: list) -> dict:
    """The user half of *messages* as the one message stream-json input takes."""
    content = []
    for message in messages:
        if message.get("role") == "user":
            content.extend(_blocks(message.get("content")))
    return {"type": "user", "message": {"role": "user", "content": content}}


def _blocks(content) -> list:
    """
    One message as the content blocks Anthropic takes.

    `core/loop.py :: _as_image` builds LiteLLM's envelopes — an `image_url` and
    a `file`, each carrying a `data:` URI — because that is what a keyed
    provider goes through. This route speaks Anthropic's own shapes, so the
    envelope is translated here rather than the file being read a second time.
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]

    blocks = []
    for piece in content or ():
        kind = piece.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": piece.get("text", "")})
        elif kind == "image_url":
            blocks.append(_attachment("image", piece["image_url"]["url"]))
        elif kind == "file":
            blocks.append(_attachment("document", piece["file"]["file_data"]))
    return blocks


def _attachment(kind: str, uri: str) -> dict:
    """A `data:` URI as a base64 source block, carrying the media type it names."""
    head, _, payload = (uri or "").partition(",")
    return {"type": kind,
            "source": {"type": "base64",
                       "media_type": head[len("data:"):].split(";")[0],
                       "data": payload}}


def _parsed(line: str):
    """One line of the stream as the event it carries, or None where it carries none."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _withheld_thinking(event: dict) -> bool:
    """
    Whether *event* is thinking the provider is doing but not showing.

    `thinking_delta` carrying no words and a token count: `sonnet` and `opus`
    on this route reason that way for minutes at a time, and a panel told
    nothing at all cannot tell that from a call that has hung.
    """
    if event.get("type") != "stream_event":
        return False
    delta = ((event.get("event") or {}).get("delta") or {})
    return (delta.get("type") == "thinking_delta"
            and not delta.get("thinking")
            and bool(delta.get("estimated_tokens")))


def _said(event: dict) -> Iterator[Chunk]:
    """
    The text one event carries.

    Deltas only: the `assistant` event repeats the whole reply once it is
    complete, and taking both would send every phase its answer twice.

    `signature_delta` arrives beside the thinking and is dropped: it signs a
    thinking block for a later turn, and every call here is a turn of its own.

    Thinking that arrives without its words yields nothing. `sonnet` and `opus`
    on this route stream `thinking_delta` carrying `""` and a rising
    `estimated_tokens`, and the words never come; `haiku` streams the words
    through this same branch. A count is not a reason: reporting it filled a
    45-minute session's transcript with 1506 lines of `thinking… ~N tokens`,
    which says how much was spent and nothing about why the run concluded what
    it did. Where the words are withheld the panel says the phase is running
    and stays quiet about the rest.
    """
    if event.get("type") != "stream_event":
        return
    streamed = event.get("event") or {}
    if streamed.get("type") != "content_block_delta":
        return
    delta = streamed.get("delta") or {}
    if delta.get("type") == "thinking_delta" and delta.get("thinking"):
        yield Chunk(REASONING, delta["thinking"])
    if delta.get("type") == "text_delta" and delta.get("text"):
        yield Chunk(TEXT, delta["text"])


def _refusal_in(event: dict, model: str):
    """
    The Refusal an event carries, or None.

    This route sees none of LiteLLM's exception classes, so `_translate` is no
    use to it: the material is the stream's own `rate_limit_event` and the
    `result` event's `stop_reason` and `subtype`.
    """
    if event.get("type") == "rate_limit_event":
        limit = event.get("rate_limit_info") or {}
        if limit.get("status") == "allowed":
            return None
        return ProviderRefused(
            "%s refused the request: the subscription's %s limit is spent, and it "
            "resets at %s. Wait for it, or run this Intent on a provider with a key."
            % (model, (limit.get("rateLimitType") or "usage").replace("_", "-"),
               _clock(limit.get("resetsAt"))),
            json.dumps(limit))

    if event.get("type") != "result":
        return None

    if event.get("stop_reason") == "max_tokens":
        return ProviderRefused(
            "%s ran out of output tokens: the reply was cut off before it finished. "
            "Ask for a smaller Step." % model,
            "stop_reason: max_tokens")

    if event.get("is_error") or event.get("subtype") != "success":
        return ProviderRefused(
            "%s refused the request: the claude CLI ended the call as '%s'."
            % (model, event.get("subtype")),
            str(event.get("result") or "")[:400])

    return None


def _clock(epoch) -> str:
    """
    `resetsAt` as a time of day.

    It arrives as an epoch, which is strictly more than
    `core/llm.py :: _retry_hint` scrapes out of a sentence with a regex, and
    strictly less use to the user until it is a clock.
    """
    try:
        return time.strftime("%H:%M", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return "the top of the next window"


def _tokens_of(usage: dict) -> dict:
    """
    Prompt, completion and cached-prompt token counts off a `result` event's
    own `usage` -- `input_tokens` and `output_tokens` on this route, where the
    keyed one reads `prompt_tokens`/`completion_tokens` off LiteLLM's `Usage`.
    `cache_read_input_tokens` is the preamble a cache hit spared the model,
    and is omitted rather than zero where nothing was cached.
    """
    tokens = {}
    if usage.get("input_tokens") is not None:
        tokens["prompt_tokens"] = usage["input_tokens"]
    if usage.get("output_tokens") is not None:
        tokens["completion_tokens"] = usage["output_tokens"]
    cached = usage.get("cache_read_input_tokens")
    if cached:
        tokens["cached_tokens"] = cached
    return tokens
