"""
The LLM client seam.

One interface, three implementations: LiteLLM against a keyed provider, the
`claude` CLI against a Claude Max subscription, and a scripted client that
replays canned turns. Everything above this line is testable without a network
or an API key.

ADR-0004 — LiteLLM is the single unified client. `tools/spike_litellm.py`
holds the evidence that it carries Anthropic thinking blocks through the
stream and honours `cache_control` — on the `anthropic/` route, which is what
`cache_control_supported` names, since the spike never exercised the others.

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
into an error, and the effort dial is the only thing there is against reaching
it. `EFFORTS` is that dial, offered in the composer and stored in settings, and
both routes are told what it says: `--effort` on the subscription route, and
`reasoning_effort` or an Anthropic thinking budget on a key. Its default is
`medium`: on one sheet through Design `high` spent 41,704 tokens of thinking
against `medium`'s 26,117 and four minutes more of the wait, and Design is one
phase of a run that has several.

The CLI takes two levels past `high`, and neither is offered. Nothing here has
measured them, and `_CEILING` — the timeout that has to move with the dial or
it cuts the answer off — is derived from measurements of the three that are.
An unmeasured level would be a ceiling made up, which is the failure that
ceiling exists to prevent.

One dial over five phases is one compromise: Design reading a drawing and
Execute writing the code for a Step it has already been given are not the
same demand, and raising the dial for the first pays for it on every call of
the second. `profile_of` is the two-way split — `thinking` for Design and
Plan, `doing` for Execute, Assertion and Review — and `ProfiledClient` is what
`for_phase` answers with: two clients, each already built on its own model
and its own effort, so `_CEILING` follows each profile's own dial rather than
the one dial shared by both. `client_from_profiles` builds it from settings;
`client_from_settings` still answers a single model on request, which is what
a session's own name and a Level 2 test that names one model both still ask
for.

`IMAGE_SUFFIXES` and `PDF_SUFFIXES` are what the providers take, and what the
attach dialog offers. A PDF is a full attachment rather than a second-class
one: drawings arrive as PDFs.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List

REASONING = "reasoning"
TEXT = "text"

_OUTPUT_BUDGET = 8192

_REASONING_EFFORT = "low"

_SUBSCRIPTION_EFFORT = "medium"

EFFORTS = ("low", "medium", "high")

_PREFIX = {
    "OpenAI": "openai",
    "Google": "gemini",
    "Anthropic": "anthropic",
    "OpenCode Go": "openai",
    "Custom/Local": "openai",
}


PROFILES = ("thinking", "doing")

_PHASES_OF_PROFILE = {
    "thinking": ("design", "plan"),
    "doing": ("execute", "assertion", "review"),
}


def profile_of(phase: str) -> str:
    """
    Which of the two profiles answers *phase*.

    `thinking` is Design and Plan: reading a drawing and turning it into a
    Step list is the demand the higher end of the dial buys headroom for.
    `doing` is Execute, Assertion and Review, which already have the Step in
    hand -- paying Design's rate on every one of them is one dial's worth of
    the wrong bill. A phase this map has never heard of is `doing`, the
    cheaper of the two guesses.
    """
    for profile, phases in _PHASES_OF_PROFILE.items():
        if phase in phases:
            return profile
    return "doing"


class ProfiledClient:
    """
    The client `core/loop.py :: _one_call` finds `for_phase` on.

    It carries no phase of its own -- `thinking` and `doing` do, each already
    built for the model and the effort its profile is set to -- and it is
    never asked to stream anything itself; `for_phase` hands back the client
    that will.
    """

    def __init__(self, thinking, doing):
        self.thinking = thinking
        self.doing = doing

    def for_phase(self, phase: str):
        return self.thinking if profile_of(phase) == "thinking" else self.doing


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


class StreamCutOff(ProviderRefused):
    """
    The provider closed the stream with no terminal chunk: no finish_reason,
    no usage, no `[DONE]`, and no exception either — the reply simply stops.

    Measured against the opencode gateway with raw HTTP and no LiteLLM in the
    call at all: of seven long streams four closed this way, after 5 to 100
    seconds of output, while three ran 45 to 180 seconds to a clean finish. A
    plain text reply carrying no reasoning is cut off as readily as a thinking
    one, so this is the connection rather than the model, and there is no
    duration to stay under (anomalyco/opencode#44148).

    Retryable, which is the only reason it is not a plain `ProviderRefused`:
    the same call sent again usually lands, and retrying is what opencode's
    own client does with it.
    """


@dataclass(frozen=True)
class Chunk:
    """One piece of a streamed reply. `kind` is REASONING or TEXT."""
    kind: str
    text: str


class Session:
    """
    One agent's conversation: a persistent message list, kept and handed back
    whole on every `turn`, so a second call to the same agent sees what it
    said and was told on the first without the caller reassembling
    `[system, user] + prior` itself (FCC-309).

    `id` is the typed identity a conversation is worked out from, in the seam
    that used to be a bare log-path string (`core/sessions.py ::
    SettingsIndex.new_log`, keyed by that path throughout). Wiring the panel's
    own Session index onto this identity is FCC-310's; this is only the
    object that identity is on, from here.
    """

    def __init__(self, id: str = None, system: str = None):
        self.id = id or uuid.uuid4().hex
        self.messages: List[dict] = []
        if system:
            self.messages.append({"role": "system", "content": system})

    def turn(self, llm, content) -> Iterator[Chunk]:
        """
        Stream a reply to *content*, added as this conversation's next user
        turn. The reply is appended to `messages` too, once the stream is
        drained, so the conversation *llm* sees on the next `turn` is the
        whole history rather than one call's worth of it.
        """
        self.messages.append({"role": "user", "content": content})
        text = []
        for chunk in llm.stream(list(self.messages)):
            if chunk.kind == TEXT:
                text.append(chunk.text)
            yield chunk
        self.messages.append({"role": "assistant", "content": "".join(text)})


class LiteLLMClient:
    """
    Streams from any provider LiteLLM supports.

    `last_cost` follows `ClaudeCodeClient`'s own contract: `None` in
    `__init__`, cleared at the top of every `stream()`, and set only for the
    call that produced it — the same reason applies here, held over a call
    that ends without a price would be billed the previous call's number.
    """

    def __init__(self, model: str, api_key: str, api_base: str = None,
                 thinking_budget: int = None, completion: Callable = None,
                 effort: str = None):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.thinking_budget = thinking_budget
        self.effort = effort
        self.last_cost = None
        self._completion = completion

    def stream(self, messages: list, **overrides) -> Iterator[Chunk]:
        """
        Stream a reply to *messages*, chunk by chunk.

        The reasoning effort is sent with `allowed_openai_params` because
        LiteLLM refuses to send a param it has no table entry for, and a
        gateway's model is exactly what it has no entry for. Usage is asked for
        on an OpenAI-compatible endpoint because a gateway that is not asked
        can end a stream without a word about why, and a cut-off reply then
        looks complete — the same `openai/` gate leaves a bare `api_base`
        model unpriced too, not because pricing is wrong but because LiteLLM
        was never asked for usage on that stream.

        Every raw chunk is kept, on top of whatever the caller already
        accumulates from the yielded Chunks, so `_priced` can reassemble them
        once the stream ends cleanly. Negligible for one phase's reply; still a
        real retention for the call's duration that did not exist before.
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
            kwargs.setdefault("reasoning_effort", _resolved_effort(self.effort))
            kwargs.setdefault("allowed_openai_params", ["reasoning_effort"])
        kwargs.setdefault("max_tokens",
                          _output_cap(self.model, (self.thinking_budget or _OUTPUT_BUDGET) * 4))
        openai_compatible = self.model.startswith("openai/")
        if openai_compatible:
            kwargs.setdefault("stream_options", {"include_usage": True})

        self.last_cost = None
        chunks = []
        finished = False
        try:
            for raw in completion(**kwargs):
                chunks.append(raw)
                for chunk in _deltas(raw):
                    yield chunk
                if _stopped_for_length(raw):
                    raise _truncated(self.model, kwargs.get("max_tokens"))
                finished = finished or _finished(raw)
        except ProviderRefused:
            raise
        except Exception as exc:
            refusal = _translate(exc, self.model)
            if refusal is None:
                raise
            raise refusal from exc

        if openai_compatible and not finished:
            raise _cut_off(self.model)

        cost, tokens = _billed(self.model, chunks, messages)
        if cost is not None or tokens is not None:
            self.last_cost = {"model": self.model}
            if cost is not None:
                self.last_cost["usd"] = cost
            if tokens is not None:
                self.last_cost.update(tokens)


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


def _billed(model, chunks, messages):
    """
    What the raw stream *chunks* would have cost against *model*'s price
    table, and the prompt/completion/cached-prompt token counts the provider
    reported, or (None, None).

    Mid-stream, LiteLLM writes cost onto `CustomStreamWrapper`'s own
    `logging_obj` — an object `stream()` never holds — so this is the
    documented manual route instead: `stream_chunk_builder` needs the full
    *messages* list to reassemble the chunks into a normal `ModelResponse`,
    which carries both the price and the `Usage` LiteLLM already parsed off
    the wire. Checked against the installed litellm (1.95.0, an
    `AdditionalPythonPackage` to the FreeCAD interpreter and not the repo's
    `.venv`).

    A model LiteLLM has no price table entry for raises out of
    `completion_cost` rather than pricing at zero — a gateway's, the same kind
    `_may_reason` and `_output_cap` already give the benefit of the doubt to —
    and that is caught here, narrowly around the price alone, so a call still
    reports the tokens it spent even where the price table has never heard of
    the model.
    """
    import litellm

    try:
        built = litellm.stream_chunk_builder(chunks, messages=messages)
    except Exception:
        return None, None

    usage = getattr(built, "usage", None)
    tokens = None
    if usage is not None:
        tokens = {"prompt_tokens": usage.prompt_tokens,
                  "completion_tokens": usage.completion_tokens}
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
        if cached:
            tokens["cached_tokens"] = cached

    try:
        cost = litellm.completion_cost(completion_response=built, model=model)
    except Exception:
        cost = None
    return cost, tokens


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


def _finished(raw) -> bool:
    """
    The terminal chunk an OpenAI-compatible endpoint owes every stream. Its
    absence over a whole stream is the connection dropped, not a short answer.
    """
    return any(getattr(choice, "finish_reason", None)
               for choice in getattr(raw, "choices", None) or [])


def _cut_off(model) -> StreamCutOff:
    return StreamCutOff(
        "%s closed the stream before it answered — no end marker, no reason given. "
        "This is the connection rather than the model; sending the Intent again "
        "usually gets through, and another provider gets through more often." % model,
        "the stream ended with no finish_reason",
    )


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


_CEILING = {"low": 300.0, "medium": 900.0, "high": 1500.0}

CALL_TIMEOUT = _CEILING[_SUBSCRIPTION_EFFORT]

_THINKING_BUDGET = {"low": 1024, "medium": 2048, "high": 4096}


def effort() -> str:
    """
    The dial, as the user last set it.

    Falls back rather than trusting what is stored: a level the settings hold
    from some older build is a flag no route can spell, and the CLI answers a
    bad `--effort` by refusing the call.
    """
    from core import settings

    chosen = settings.instance().get_effort()
    return chosen if chosen in EFFORTS else _SUBSCRIPTION_EFFORT


def _resolved_effort(explicit: str = None) -> str:
    """*explicit* when it is one `EFFORTS` offers; the dial in force otherwise."""
    return explicit if explicit in EFFORTS else effort()


def effort_for_profile(profile: str) -> str:
    """
    The dial *profile* -- `thinking` or `doing` -- is set to.

    Reads the profile's own setting where one exists, and the old single dial
    a user who has never split the two still has: `settings_manager ::
    get_profile_effort` is what carries that fallback, so a level from an
    older build or an empty store both land on `_resolved_effort`'s own
    default the same way `effort()` already does for the single dial.
    """
    from core import settings

    return _resolved_effort(settings.instance().get_profile_effort(profile))


def effort_is_honoured(model_id: str = None) -> bool:
    """
    Whether asking *model_id* to think harder changes anything at all.

    The subscription route always honours it -- `--effort` is the CLI's own
    flag -- and so does Anthropic on a key, where the dial moves the thinking
    budget. Anywhere else it is LiteLLM's table that knows, and the table
    lives behind an import that costs seconds: the panel asks this while it is
    being built, so the question is only put to LiteLLM when something else
    has already paid to load it. Unknown means yes, the same benefit of the
    doubt `_may_reason` gives a gateway's own model.
    """
    import sys

    from core import model_catalog, settings

    try:
        provider, mid, _ = settings.instance().get_llm_credentials(model_id)
    except Exception:
        return True
    if model_catalog.needs_no_key(provider) or provider == "Anthropic":
        return True
    if "litellm" not in sys.modules:
        return True
    return _may_reason(litellm_model_name(provider, mid))

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


class ScriptedLLMClient:
    """
    The test double for the seam. Hand it the turns you want back, in order,
    and it records the messages it was asked with.

        client = ScriptedLLMClient(["a plan", {"reasoning": "hmm", "text": "code"}])
    """

    def __init__(self, turns: Iterable = ()):
        self.model = "scripted"
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


def cache_control_supported(model: str) -> bool:
    """
    Whether a `cache_control` block on a message reaches the wire as a cache
    breakpoint rather than as a key the provider was never asked about.

    `tools/spike_litellm.py` is the evidence, and it is narrower than ADR-0004's
    own claim: LiteLLM carries `cache_control` through to
    `AnthropicConfig.transform_request` for a model routed through Anthropic's
    own API, which is `model.startswith("anthropic/")` and nothing else here —
    not the `openai/` gateway, not a bare `api_base` model LiteLLM has never
    heard of, and not `ClaudeCodeClient`'s subscription CLI, which never spoke
    to this transform in the first place. Guessing yes for one of those risks
    nothing on the wire; guessing no costs one field nobody reads.
    """
    return bool(model) and model.startswith("anthropic/")


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
    from core import settings

    provider, mid, _ = settings.instance().get_llm_credentials(model_id)
    return litellm_model_name(provider, mid)


def client_from_settings(model_id: str = None, effort: str = None):
    """
    Build a client from the provider settings page and stored keys.

    A local server's address is a setting of its own rather than the key field,
    and a gateway's is fixed and the catalog knows it. LiteLLM wants a key even
    where the local server ignores it — and has no route at all to a
    subscription, which is why one provider is answered by a client of its own
    before any of that is read.

    *effort* is the resolved dial one client of `client_from_profiles` is
    built at; left `None` the client falls back to the single dial in force
    the way it always has, which is what every caller outside a profile does.
    """
    from core import model_catalog, settings

    sm = settings.instance()
    provider, mid, secret = sm.get_llm_credentials(model_id)

    if model_catalog.needs_no_key(provider):
        return ClaudeCodeClient(model=mid, effort=effort)

    api_base = model_catalog.api_base_for(provider, sm.get_base_url_for_provider(provider))
    api_key = secret or ("not-needed" if api_base else "")

    return LiteLLMClient(
        model=litellm_model_name(provider, mid),
        api_key=api_key,
        api_base=api_base,
        thinking_budget=2048 if provider == "Anthropic" else None,
        effort=effort,
    )


def client_from_profiles() -> ProfiledClient:
    """
    The client a run is actually made with: one built per profile, each on
    the model and the effort that profile is set to, wired so `for_phase`
    answers every phase from the one that owns it.
    """
    from core import settings

    sm = settings.instance()
    return ProfiledClient(
        thinking=client_from_settings(sm.get_profile_model("thinking"),
                                      effort_for_profile("thinking")),
        doing=client_from_settings(sm.get_profile_model("doing"),
                                   effort_for_profile("doing")),
    )


ROLES = ("orchestrator", "design", "plan", "execute", "assertion", "review",
         "sub-agent worker")

DEFAULT_ROLE_TABLE = (
    {"role": "orchestrator", "provider": "Anthropic",
     "model": "claude-sonnet-5", "effort": "low"},
    {"role": "design", "provider": "Anthropic",
     "model": "claude-opus-5", "effort": "high"},
    {"role": "plan", "provider": "Anthropic",
     "model": "claude-opus-5", "effort": "high"},
    {"role": "execute", "provider": "Anthropic",
     "model": "claude-sonnet-5", "effort": "medium"},
    {"role": "assertion", "provider": "Anthropic",
     "model": "claude-sonnet-5", "effort": "medium"},
    {"role": "review", "provider": "Anthropic",
     "model": "claude-sonnet-5", "effort": "medium"},
    {"role": "sub-agent worker", "provider": "Anthropic",
     "model": "claude-haiku-4-5", "effort": "low"},
)
"""`docs/rlm-role-model-table.md` (FCC-293), as the row `settings_manager ::
get_role_table` seeds an empty store with. Kept here rather than in
`settings_manager` because the value each row resolves to -- a provider,
model and effort triple `client_from_settings` already reads -- is this
module's vocabulary, not the settings store's."""


class RoleClient:
    """
    The client a run is actually made with: one built per role the table
    names, wired so `for_phase` answers a phase from the row that names it
    (FCC-312), replacing the two-profile split `client_from_profiles` drew.

    A role the table has never heard of -- no phase, the orchestrator or a
    sub-agent worker call this module has no site for yet, since the table
    itself carries every role the epic named -- answers from `caller`, the
    client the caller of `for_phase`/`for_role` was already running under,
    not a cheaper default: the epic's settled fallback rule.
    """

    def __init__(self, roles: dict, caller):
        self.roles = roles
        self.caller = caller

    def for_role(self, role: str):
        return self.roles.get(role, self.caller)

    def for_phase(self, phase: str):
        return self.for_role(phase)


def client_from_roles() -> RoleClient:
    """
    Build `RoleClient` from the settings table (FCC-312): one client per row,
    keyed by that row's role, and `caller` -- the fallback a role absent from
    the table answers from -- built on the same model/effort a caller with no
    per-role split would already be running under.
    """
    from utils import settings_manager

    sm = settings_manager.instance()
    roles = {
        row["role"]: client_from_settings(row["model"], row.get("effort"))
        for row in sm.get_role_table()
    }
    return RoleClient(roles=roles, caller=client_from_settings())
