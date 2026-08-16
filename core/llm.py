"""
The LLM client seam.

One interface, three implementations: LiteLLM against a keyed provider, the
`claude` CLI against a Claude Max subscription, and a scripted client that
replays canned turns. Everything above this line is testable without a network
or an API key.

ADR-0004 — LiteLLM is the single unified client. `tools/spike_litellm.py`
holds the evidence that it carries Anthropic thinking blocks through the
stream and honours `cache_control`.

ADR-0025 amends it in part. `ClaudeCodeClient` answers a phase on a
subscription the user already pays for, as a subprocess and not a package —
nothing is added to FreeCAD's shared interpreter, where the Agent SDK would add
one that wraps the same CLI. It shows no reasoning, and that silence is scoped
to the runs a user chooses to spend a subscription on rather than imposed on
every provider: Anthropic through a key keeps its thinking stream.

This module must stay importable without FreeCAD; the settings lookup that
needs it is deferred into `client_from_settings`.

A reasoning model left on the provider's default cap spends it all on thought
and answers with nothing, so `_OUTPUT_BUDGET` sets one; read it like the
Anthropic thinking budget, because the request may cost four times it. Told
nothing about effort, kimi-k3 circled one drawing for ten minutes and never
left its own reasoning, and "medium" bought no less of it — the cap turns that
into an error, and `_REASONING_EFFORT` is the only dial there is against
reaching it. `_SUBSCRIPTION_EFFORT` is the other end of the same dial, for the
route that is not billed by the token: there the circling is visible, because
the reasoning streams, and it is bounded, because the call has a ceiling — so
the low setting would only buy the cheapest thinking a subscription can.

`IMAGE_SUFFIXES` and `PDF_SUFFIXES` are what the providers take, and what the
attach dialog offers. A PDF is a full attachment rather than a second-class
one: drawings arrive as PDFs.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List

REASONING = "reasoning"
TEXT = "text"

_OUTPUT_BUDGET = 8192

_REASONING_EFFORT = "low"

_SUBSCRIPTION_EFFORT = "high"

_THINKING_WITHHELD = "thinking… ~%d tokens\n"

_PREFIX = {
    "OpenAI": "openai",
    "Google": "gemini",
    "Anthropic": "anthropic",
    "OpenCode Go": "openai",
    "Custom/Local": "openai",
}


class ProviderRefused(Exception):
    """
    The provider would not answer: a limit, a rejected key, a dropped
    connection, a reply cut off before it finished.

    Carries a sentence written for the user — which model, which limit, what to
    do about it — and `said`, what the provider itself replied, verbatim.
    """

    def __init__(self, message: str, said: str = ""):
        super().__init__(message)
        self.said = said


@dataclass(frozen=True)
class Chunk:
    """One piece of a streamed reply. `kind` is REASONING or TEXT."""
    kind: str
    text: str


class LiteLLMClient:
    """Streams from any provider LiteLLM supports."""

    def __init__(self, model: str, api_key: str, api_base: str = None,
                 thinking_budget: int = None, completion: Callable = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.thinking_budget = thinking_budget
        self._completion = completion

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        """
        Stream a reply to *messages*, chunk by chunk.

        The reasoning effort is sent with `allowed_openai_params` because
        LiteLLM refuses to send a param it has no table entry for, and a
        gateway's model is exactly what it has no entry for. Usage is asked for
        on an OpenAI-compatible endpoint because a gateway that is not asked
        can end a stream without a word about why, and a cut-off reply then
        looks complete.
        """
        completion = self._completion
        if completion is None:
            import litellm
            completion = litellm.completion

        kwargs = dict(
            model=self.model,
            messages=messages,
            stream=True,
            api_key=self.api_key,
            **overrides,
        )
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.thinking_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        elif _may_reason(self.model):
            kwargs.setdefault("reasoning_effort", _REASONING_EFFORT)
            kwargs.setdefault("allowed_openai_params", ["reasoning_effort"])
        kwargs.setdefault("max_tokens",
                          _output_cap(self.model, (self.thinking_budget or _OUTPUT_BUDGET) * 4))
        if self.model.startswith("openai/"):
            kwargs.setdefault("stream_options", {"include_usage": True})

        try:
            for raw in completion(**kwargs):
                for chunk in _deltas(raw):
                    yield chunk
                if _stopped_for_length(raw):
                    raise _truncated(self.model, kwargs.get("max_tokens"))
        except ProviderRefused:
            raise
        except Exception as exc:
            refusal = _translate(exc, self.model)
            if refusal is None:
                raise
            raise refusal from exc


def _may_reason(model) -> bool:
    """
    Whether to tell *model* how hard to think.

    A model LiteLLM knows answers for itself. One it has never heard of is a
    gateway's — kimi-k3 is — and those are the ones that need telling; a local
    server that does not reason ignores the key rather than refusing it.
    """
    import litellm

    if litellm.supports_reasoning(model):
        return True
    try:
        litellm.get_model_info(model)
    except Exception:
        return True
    return False


def _output_cap(model, wanted) -> int:
    """
    *wanted*, or the model's own ceiling where LiteLLM knows one: asking for
    more output than a model can write is rejected outright, and a gateway
    model LiteLLM has never heard of has no ceiling to read.
    """
    import litellm

    try:
        known = litellm.get_model_info(model).get("max_output_tokens")
    except Exception:
        known = None
    return min(wanted, known) if known else wanted


def _deltas(raw) -> Iterator[Chunk]:
    """Map one LiteLLM stream chunk onto our Chunks, provider-agnostically."""
    for choice in getattr(raw, "choices", None) or []:
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield Chunk(REASONING, reasoning)
        text = getattr(delta, "content", None)
        if text:
            yield Chunk(TEXT, text)


def _stopped_for_length(raw) -> bool:
    """The provider marking a reply as cut off. Dropped, this looks like a bad answer."""
    return any(getattr(choice, "finish_reason", None) == "length"
               for choice in getattr(raw, "choices", None) or [])


def _truncated(model, cap) -> ProviderRefused:
    """The cap is `budget * 4` above and invisible from outside; the message names it."""
    where = "at the %d-token output cap" % cap if cap else "at the provider's default output cap"
    return ProviderRefused(
        "%s ran out of output tokens: the reply was cut off %s before it finished. "
        "Raise the cap or ask for a smaller Step." % (model, where),
        "finish_reason: length",
    )


_RETRY_AFTER = re.compile(
    r"retry[-_ ]?(?:delay|after)\"?\s*[:=]?\s*\"?(\d+(?:\.\d+)?\s*[smh]?)", re.I)
_OUT_OF_CREDIT = ("insufficient_quota", "credit balance", "out of credit", "billing_hard_limit")
_OUT_OF_QUOTA = ("quota", "resource_exhausted", "resource has been exhausted")
_USED_UP = ("usage limit", "monthly limit", "limit reached", "spend limit",
            "spending limit")
_PER_MINUTE = ("perminute", "per minute", "per min")
_NO_IMAGES = ("image_url", "image input", "does not support image", "image content")


def _translate(exc, model):
    """
    A LiteLLM exception as a sentence the user can act on, or None if it is not one.

    A plan's own ceiling is not a rate, though a gateway answers it with a 429
    too, and telling the user to wait a moment is telling them to wait ten
    days: the opencode gateway refused kimi-k3 mid-run with "Monthly usage
    limit reached. Resets in 10 days." Google answers a per-minute limit with
    the same RESOURCE_EXHAUSTED body as a spent daily quota, so only the
    violated metric says whether the wait is seconds or a day.
    """
    import litellm

    said = str(getattr(exc, "message", "") or exc)
    status = getattr(exc, "status_code", None)
    if status:
        said = "HTTP %s %s" % (status, said)
    low = said.lower()
    refused = "%s refused the request" % model

    if isinstance(exc, litellm.ContextWindowExceededError):
        return ProviderRefused(
            refused + ": the request is longer than the model's context window. Break the "
            "Intent into smaller pieces, or pick a model with more room.", said)
    if isinstance(exc, litellm.AuthenticationError):
        return ProviderRefused(
            refused + ": the API key was rejected. Check the key for this provider on the "
            "Providers page.", said)
    if isinstance(exc, (litellm.RateLimitError, litellm.BudgetExceededError)):
        if any(word in low for word in _OUT_OF_CREDIT):
            return ProviderRefused(
                refused + ": the account is out of credit. Top it up in your provider's "
                "billing dashboard.", said)
        if any(word in low for word in _USED_UP):
            return ProviderRefused(
                refused + ": the plan's usage limit for this model is reached, and waiting "
                "a moment will not clear it. The provider said: %s" % said, said)
        if any(word in low for word in _OUT_OF_QUOTA) and not any(
                word in low for word in _PER_MINUTE):
            return ProviderRefused(
                refused + ": your quota for this model is exhausted.%s Wait for the quota "
                "window to reset, raise the quota with your provider, or run this on another "
                "model." % _retry_hint(said), said)
        return ProviderRefused(
            refused + ": you are being rate limited — too many requests too quickly.%s Wait "
            "and send the Intent again." % _retry_hint(said), said)
    if isinstance(exc, litellm.Timeout):
        return ProviderRefused(
            refused + ": the provider did not answer in time. Try again, or pick a faster "
            "model.", said)
    if isinstance(exc, litellm.APIConnectionError):
        return ProviderRefused(
            "Could not reach %s: the connection failed. This is a transport failure, not a "
            "limit on your account — check the endpoint is running and reachable." % model, said)
    if isinstance(exc, litellm.BadRequestError) and any(word in low for word in _NO_IMAGES):
        return ProviderRefused(
            refused + ": this model cannot accept images. Send the Intent without the "
            "attachment, or pick a model with image support.", said)
    return None


def _retry_hint(said):
    """The reset time, when the provider named one — the difference between waiting and giving up."""
    found = _RETRY_AFTER.search(said)
    if not found:
        return ""
    return " The provider says to retry in %s." % found.group(1).strip()


CALL_TIMEOUT = 600.0

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
    """
    Streams from a Claude Max subscription, through one `claude` process a call.

    Every flag it sends is a measurement rather than a taste. `--verbose`
    because `--output-format stream-json` refuses to run under `-p` without it.
    `--disallowed-tools` naming every tool the CLI reports is the whole of the
    29,213-to-2,385 token cut in the preamble — `--allowed-tools ''` strips
    nothing and the 21,623 arrive as a cache read instead. `--safe-mode` rather
    than `--bare`, because `--bare` forces `ANTHROPIC_API_KEY` and so breaks
    subscription auth outright; `--strict-mcp-config` and `--setting-sources ''`
    so that a machine's MCP servers and a repository's `CLAUDE.md` cannot walk
    into a Design prompt. `ANTHROPIC_API_KEY` is kept out of the child's
    environment for the same reason the provider exists: a key in the shell
    would put the run back on the meter under a label that says subscription.

    `last_cost` is the one fact that crosses this seam and is not a `Chunk`.
    `stream()` yields only Chunks and the client never sees the EventStream, so
    `core/loop.py :: _streamed` — the one place every phase's stream is
    consumed — reads it off the client and emits from there.
    `ScriptedLLMClient` carries none. It is what *this* call spent and is
    cleared as the call starts: held over, a call that ends without a `result`
    event would be billed the last one's number.

    What the provider withholds is not the same as what it did not do: `sonnet`
    streams its thinking as `thinking_delta` carrying `""` and an
    `estimated_tokens` count — 245 thinking tokens on one recorded call, and
    not a word of them — where `haiku` streams the words. Both reach the panel:
    the words where there are words, and the count where there are none, which
    is a measurement the provider made rather than a timer standing in for one.

    `_DISALLOWED_TOOLS` is the tool list one CLI build reported. Naming a tool
    a build does not have costs nothing, and a tool a later build adds is a
    tool this list does not yet name — the recording it was read off is kept
    beside the tests for that comparison to stay possible.

    Reasoning crosses this seam like any other provider's: `_said` maps
    `thinking_delta` onto REASONING and `text_delta` onto TEXT, and the panel
    renders them through the same path it renders LiteLLM's `reasoning_content`
    with. Whether there is any is what `--effort` decides, which is this
    module's own dial and not a property of the route — the same call answers
    with 160 thinking tokens at `high` and none at `low`.

    `timeout` is a ceiling and not a measurement. The CLI has none of its own
    where the keyed route leans on LiteLLM's, and a subprocess that never
    answers is the parent's problem. Ten minutes is per call, not per run,
    because a whole run of all its phases takes a quarter of an hour.
    """

    def __init__(self, model: str, spawn: Callable = None,
                 timeout: float = CALL_TIMEOUT):
        self.model = model
        self.timeout = timeout
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
                    spawn(_invocation(self.model, system), workspace), messages):
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

            for line in process.stdout:
                event = _parsed(line)
                if event is None:
                    continue
                refusal = _refusal_in(event, self.model)
                if refusal is not None:
                    raise refusal
                for chunk in _said(event):
                    yield chunk
                if event.get("total_cost_usd") is not None:
                    self.last_cost = {"usd": event["total_cost_usd"],
                                      "model": self.model}

            code = process.wait()
            if overdue.is_set():
                raise ProviderRefused(
                    "%s refused the request: the provider did not answer in time. Try "
                    "again, or ask for a smaller Step." % self.model,
                    "no reply in %g seconds" % self.timeout)
            if code:
                raise ProviderRefused(
                    "%s could not be reached: the claude CLI exited with status %s. "
                    "Check it is installed and signed in with `claude auth status`."
                    % (self.model, code),
                    (process.stderr.read() or "").strip()[:400])
        finally:
            ceiling.cancel()
            process.kill()


def _spawn_claude(argv: list, cwd: str):
    """
    One `claude` per call, with its own pipes and no key in its environment.

    The pipes are decoded as UTF-8 rather than by the locale: inside FreeCAD.app
    neither LANG nor LC_ALL is set, so the locale resolves to ascii and the
    first non-ASCII byte kills the read.
    """
    return subprocess.Popen(
        argv,
        cwd=cwd,
        env=_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _invocation(model: str, system_prompt: str) -> list:
    """
    The command line one call is made with, in the order it was measured.

    The effort is `_SUBSCRIPTION_EFFORT` and not `_REASONING_EFFORT`: the low
    one exists against a model circling invisibly in its own reasoning, and here
    the circling streams and the call is bounded, so what low would still do is
    buy the cheapest thinking on the one route that does not bill it by the
    token.

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
        "--effort", _SUBSCRIPTION_EFFORT,
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


def _said(event: dict) -> Iterator[Chunk]:
    """
    The text one event carries.

    Deltas only: the `assistant` event repeats the whole reply once it is
    complete, and taking both would send every phase its answer twice.

    `signature_delta` arrives beside the thinking and is dropped: it signs a
    thinking block for a later turn, and every call here is a turn of its own.

    Thinking that arrives without its words is reported as the count the
    provider does give. `sonnet` streams `thinking_delta` carrying `""` and a
    rising `estimated_tokens`; the words never come, and the run is thinking all
    the same. The count is what the provider measured, not a timer standing in
    for one.
    """
    if event.get("type") != "stream_event":
        return
    streamed = event.get("event") or {}
    if streamed.get("type") != "content_block_delta":
        return
    delta = streamed.get("delta") or {}
    if delta.get("type") == "thinking_delta":
        if delta.get("thinking"):
            yield Chunk(REASONING, delta["thinking"])
        elif delta.get("estimated_tokens"):
            yield Chunk(REASONING, _THINKING_WITHHELD % delta["estimated_tokens"])
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


class ScriptedLLMClient:
    """
    The test double for the seam. Hand it the turns you want back, in order,
    and it records the messages it was asked with.

        client = ScriptedLLMClient(["a plan", {"reasoning": "hmm", "text": "code"}])
    """

    def __init__(self, turns: Iterable = ()):
        self.turns: List = list(turns)
        self.calls: List[list] = []

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        self.calls.append(messages)
        if not self.turns:
            raise AssertionError(
                "ScriptedLLMClient ran out of turns on call %d" % len(self.calls)
            )
        return _replay(self.turns.pop(0))


def _replay(turn) -> Iterator[Chunk]:
    """An Exception anywhere in a turn is raised there — a stream can die partway."""
    if isinstance(turn, Exception):
        raise turn
    for chunk in _as_chunks(turn):
        if isinstance(chunk, Exception):
            raise chunk
        yield chunk


def _as_chunks(turn) -> List[Chunk]:
    if isinstance(turn, str):
        return [Chunk(TEXT, turn)]
    if isinstance(turn, dict):
        chunks = []
        if turn.get("reasoning"):
            chunks.append(Chunk(REASONING, turn["reasoning"]))
        if turn.get("text"):
            chunks.append(Chunk(TEXT, turn["text"]))
        return chunks
    return list(turn)


def litellm_model_name(provider: str, model_id: str) -> str:
    """'Anthropic', 'claude-3-7-sonnet-20250219' -> 'anthropic/claude-3-7-...'."""
    prefix = _PREFIX.get(provider)
    if prefix is None:
        return model_id
    if model_id.startswith(prefix + "/"):
        return model_id
    return prefix + "/" + model_id


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
PDF_SUFFIXES = (".pdf",)


def accepts_images(model: str):
    """
    Whether *model* takes images: True, False, or None when LiteLLM has never
    heard of it — which a Custom/Local model is, and which is not a no.
    """
    import litellm

    try:
        litellm.get_model_info(model)
    except Exception:
        return None
    return bool(litellm.supports_vision(model))


def accepts_pdf(model: str):
    """
    The exact twin of `accepts_images`, over the same table.

    Anthropic, OpenAI, Vertex AI, Bedrock and Mistral take a PDF on
    `/chat/completions`; None for a model LiteLLM has never heard of, because a
    Custom/Local model is not a no.

    Read off `get_model_info` rather than through `litellm.supports_pdf_input`:
    the flag is in the table in every version, the top-level predicate is not —
    the LiteLLM this addon ships against (1.95.0) has the one and not the other.
    """
    import litellm

    try:
        info = litellm.get_model_info(model)
    except Exception:
        return None
    return bool(info.get("supports_pdf_input"))


def unusable_attachments(paths, model: str) -> list:
    """
    One sentence per attachment this run would not be able to use, before it
    starts. Empty means send it.

    The provider's own complaint arrives after the phase has already been
    called, which is too late to be told the picture was ignored (FCC-14).

    A PDF is not an image, so a run carrying nothing but PDFs is not refused
    for a model that cannot see — the PDF check is the one that decides it.
    """
    reasons = []
    pdfs, pictures = [], []
    for path in paths:
        name = os.path.basename(path)
        suffix = os.path.splitext(path)[1].lower()
        if not os.path.isfile(path):
            reasons.append(
                "%s is not there any more, so there would be nothing to send." % name)
        elif suffix in PDF_SUFFIXES:
            pdfs.append(name)
        elif suffix in IMAGE_SUFFIXES:
            pictures.append(name)
        else:
            reasons.append(
                "%s is not something the provider will take. Attach a PNG, JPEG, GIF, "
                "WebP or PDF instead." % name)

    nothing_but_pdfs = bool(pdfs) and not pictures
    if paths and not nothing_but_pdfs and accepts_images(model) is False:
        reasons.append(
            "%s cannot accept images. Send the Intent without the attachment, or pick a "
            "model with image support." % model)
    if pdfs and accepts_pdf(model) is False:
        reasons.append(
            "%s cannot read a PDF, so %s would not reach it. Attach an image of the "
            "drawing instead, or pick a model that reads PDFs."
            % (model, ", ".join(pdfs)))
    return reasons


def selected_model_name(model_id: str = None) -> str:
    """The model the panel is set to, spelled the way LiteLLM spells it."""
    from utils import settings_manager

    provider, mid, _ = settings_manager.instance().get_llm_credentials(model_id)
    return litellm_model_name(provider, mid)


def client_from_settings(model_id: str = None):
    """
    Build a client from the provider settings page and stored keys.

    A local server's address is a setting of its own rather than the key field,
    and a gateway's is fixed and the catalog knows it. LiteLLM wants a key even
    where the local server ignores it — and has no route at all to a
    subscription, which is why one provider is answered by a client of its own
    before any of that is read.
    """
    from utils import settings_manager

    from core import model_catalog

    sm = settings_manager.instance()
    provider, mid, secret = sm.get_llm_credentials(model_id)

    if model_catalog.needs_no_key(provider):
        return ClaudeCodeClient(model=mid)

    api_base = model_catalog.api_base_for(provider, sm.get_base_url_for_provider(provider))
    api_key = secret or ("not-needed" if api_base else "")

    return LiteLLMClient(
        model=litellm_model_name(provider, mid),
        api_key=api_key,
        api_base=api_base,
        thinking_budget=2048 if provider == "Anthropic" else None,
    )
