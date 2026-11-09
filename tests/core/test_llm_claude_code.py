"""
Level 1 — the Claude subscription client, over two recorded `claude -p` runs.

`tests/recorded/claude-code-stream-minimal.jsonl` is one real call under the
flag set this client sends: 2,385 tokens of preamble, `apiKeySource: none`, two
surviving tools, a `rate_limit_event` and a `result`.
`claude-code-stream-default-env.jsonl` is the same prompt with no flags but the
model — 29,213 tokens and 74 tools — and is what the tool count without
`--tools ""` is measured against.

What spawns the process is injected the way `LiteLLMClient` takes its
`completion` callable, so nothing here runs a binary, spends a call or needs a
subscription. The two cases no recording could carry — a spent window and a
reply cut off — are the recorded transcript with the one field that says so
changed, because provoking either costs a five-hour window or a 64,000-token
reply.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import pytest
from core.loop.prompt import _as_image

from conftest import collect

from core import loop
from core.llm import (
    REASONING,
    TEXT,
    Chunk,
    ClaudeCodeClient,
    CallStalled,
    ProviderRefused,
    find_claude,
    litellm_model_name,
    signed_in,
    unusable_attachments,
    effort,
)
from core.llm import claude as llm_claude
from core.llm.claude import _spawn_claude, _environment, _CLAUDE_CANDIDATES
from core.llm.claude_stream import _user_turn
from core.llm.effort_policy import _CEILING, _SUBSCRIPTION_EFFORT
from core.llm.attachments import _PREFIX
from tests import modelmania_harness

RECORDED = modelmania_harness.RECORDED_DIR

MINIMAL = "claude-code-stream-minimal.jsonl"
DEFAULT_ENV = "claude-code-stream-default-env.jsonl"
THINKING_REDACTED = "claude-code-stream-thinking.jsonl"
THINKING_IN_WORDS = "claude-code-stream-thinking-in-words.jsonl"
DESIGN_AT_EFFORT = "claude-code-design-effort.jsonl"

MODEL = "sonnet"

PROVIDER = "Claude (subscription)"


def _lines(name):
    with open(os.path.join(RECORDED, name), encoding="utf-8") as handle:
        return handle.readlines()


def _events(name):
    return [json.loads(line) for line in _lines(name) if line.strip()]


def _init(name):
    return [event for event in _events(name) if event.get("subtype") == "init"][0]


def _result(name):
    return [event for event in _events(name) if event.get("type") == "result"][0]


def _design_at(effort):
    """
    One recorded Design call, by the effort it was made at.

    The same sheet and the same prompt through `--effort low`, `medium` and
    `high` on Opus, kept as the `result` events the CLI ended each of them with;
    `xhigh` is the longest Design call of a `mm2025-phase1` run on Haiku 5.5.
    """
    return [call for call in _events(DESIGN_AT_EFFORT)
            if call["effort"] == effort][0]["result"]


def _seconds(result):
    return result["duration_ms"] / 1000.0


class _Pipe(io.StringIO):
    """A pipe the test can still read after the client has closed it."""

    def close(self):
        pass


class _Process:
    """`subprocess.Popen`, replaying a recorded transcript down its stdout."""

    def __init__(self, lines, returncode=0, stderr=""):
        self.stdin = _Pipe()
        self.stdout = iter(lines)
        self.stderr = _Pipe(stderr)
        self.returncode = returncode
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.returncode


class _Silence:
    """A stdout with nothing on the other end: it ends when the process is killed."""

    def __init__(self, killed):
        self._killed = killed

    def __iter__(self):
        self._killed.wait(10)
        return iter(())


class _ProcessThatNeverAnswers:
    """A `claude` that starts, writes nothing, and would sit there all day."""

    def __init__(self):
        self.stdin = _Pipe()
        self.stderr = _Pipe()
        self.returncode = 0
        self.killed = threading.Event()
        self.stdout = _Silence(self.killed)

    def kill(self):
        self.killed.set()

    def wait(self, timeout=None):
        return self.returncode


def client_over(process, **kwargs):
    """
    A client whose call runs *process*, and the spawn that recorded how.

    Everything the invocation says is read at spawn time, which is when the
    real process reads it: the system prompt file still exists then, and so
    does the directory it was started in.
    """
    def spawn(argv, cwd):
        spawn.argv = list(argv)
        spawn.cwd = cwd
        spawn.beside_it = sorted(os.listdir(cwd))
        with open(argv[argv.index("--system-prompt-file") + 1], encoding="utf-8") as handle:
            spawn.system = handle.read()
        spawn.process = process
        return process

    return ClaudeCodeClient(model=MODEL, spawn=spawn, **kwargs), spawn


def client_replaying(name=MINIMAL, **kwargs):
    return client_over(_Process(_lines(name)), **kwargs)


def a_turn(user="Make a 10mm plate", system="You are the Plan phase."):
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def sent(spawn):
    """The one JSON message the client wrote to the process's stdin."""
    return json.loads(spawn.process.stdin.getvalue())


def _changed(name, **fields):
    """The recorded transcript with one field of its `result` event replaced."""
    lines = []
    for line in _lines(name):
        event = json.loads(line)
        if event.get("type") == "result":
            event.update(fields)
        lines.append(json.dumps(event) + "\n")
    return lines


def _rate_limited(name, status="rejected"):
    """The recorded transcript with its `rate_limit_event` refusing the call."""
    lines = []
    for line in _lines(name):
        event = json.loads(line)
        if event.get("type") == "rate_limit_event":
            event["rate_limit_info"]["status"] = status
        lines.append(json.dumps(event) + "\n")
    return lines


def test_the_recorded_answer_arrives_as_text():
    client, _ = client_replaying()

    reply = collect(client.stream(a_turn()))

    assert reply.text == _result(MINIMAL)["result"]


def test_the_thinking_arrives_as_reasoning_before_the_answer():
    """
    Recorded off `haiku` at high effort, which streams its thinking in words:
    fifteen `thinking_delta` carrying text, then the answer. `signature_delta`
    signs the block for a later turn and `input_json_delta` belongs to a tool
    call; a phase reads neither.
    """
    client, _ = client_replaying(THINKING_IN_WORDS)

    chunks = list(client.stream(a_turn()))
    kinds = [chunk.kind for chunk in chunks]

    assert set(kinds) == {REASONING, TEXT}
    assert kinds.index(REASONING) < kinds.index(TEXT)
    assert kinds.count(REASONING) == 15
    assert "".join(c.text for c in chunks if c.kind == REASONING).startswith("The user is asking")


def test_thinking_the_provider_withholds_the_words_of_is_reported_once_and_wordlessly():
    """
    `sonnet` streams the thinking it did without its words: `thinking_delta`
    carrying `""` and a rising `estimated_tokens`, 245 thinking tokens on the
    `result`, and not a word of them.

    One wordless chunk reaches the panel, whatever the number of deltas. That
    the model is reasoning is worth a row on a phase that takes minutes; the
    count is not, and reported per delta it filled one 45-minute session with
    1506 lines saying how much rather than why.
    """
    client, _ = client_replaying(THINKING_REDACTED)

    chunks = list(client.stream(a_turn()))
    thought = [chunk.text for chunk in chunks if chunk.kind == REASONING]

    assert _result(THINKING_REDACTED)["usage"]["output_tokens_details"]["thinking_tokens"] == 245
    assert thought == [""]
    assert TEXT in {chunk.kind for chunk in chunks}


def test_three_withheld_deltas_are_still_one_chunk():
    """
    The recording carries three `thinking_delta`s -- two with a count, one with
    neither words nor a count. A phase reasoning is one fact however many
    deltas carry it, and the third says nothing even by that measure.
    """
    client, _ = client_replaying(THINKING_REDACTED)

    reasoning = [c for c in client.stream(a_turn()) if c.kind == REASONING]

    withheld = [event for event in _events(THINKING_REDACTED)
                if (((event.get("event") or {}).get("delta") or {}).get("type") == "thinking_delta")]
    assert len(withheld) == 3
    assert reasoning == [Chunk(REASONING, "")]


def test_a_reply_with_no_thinking_in_it_is_all_text():
    """The effort dial decides whether there is any; nothing here manufactures one."""
    client, _ = client_replaying()

    kinds = {chunk.kind for chunk in client.stream(a_turn())}

    assert kinds == {TEXT}


def test_the_text_arrives_in_the_pieces_the_provider_sent_it_in():
    """The panel fills as the answer forms, so the deltas are not aggregated."""
    client, _ = client_replaying()

    chunks = list(client.stream(a_turn()))

    assert len(chunks) == 5


def test_nothing_is_spawned_before_the_caller_pulls():
    client, spawn = client_replaying()

    stream = client.stream(a_turn())

    assert not hasattr(spawn, "argv")
    next(stream)
    assert spawn.argv


def test_what_the_call_spent_is_carried_off_the_result_event():
    """
    `stream()` yields Chunks and the client never sees the EventStream, so what
    the call spent crosses the seam as an attribute of the client. The
    `result` event's own `usage` carries the token counts beside the price:
    `input_tokens`, `output_tokens`, and `cache_read_input_tokens` where the
    preamble was cached.
    """
    client, _ = client_replaying()

    collect(client.stream(a_turn()))

    usage = _result(MINIMAL)["usage"]
    assert client.last_cost == {
        "usd": _result(MINIMAL)["total_cost_usd"], "model": MODEL,
        "prompt_tokens": usage["input_tokens"],
        "completion_tokens": usage["output_tokens"],
        "cached_tokens": usage["cache_read_input_tokens"],
        "resolved_model": next(iter(_result(MINIMAL)["modelUsage"])),
    }


def test_tokens_of_carries_cache_creation_tokens_when_reported():
    """
    `cache_creation_input_tokens` is what a call paid to write the preamble a
    later call can then read from cache -- the pair `cache_read_input_tokens`
    reports what an earlier one already wrote. A `result` event that carries
    both is read as both.
    """
    from core.llm.claude_stream import _tokens_of

    tokens = _tokens_of({"input_tokens": 2, "output_tokens": 238,
                         "cache_read_input_tokens": 2385,
                         "cache_creation_input_tokens": 29213})

    assert tokens["cache_creation_tokens"] == 29213
    assert tokens["cached_tokens"] == 2385


def test_tokens_of_omits_cache_creation_tokens_when_nothing_was_written():
    from core.llm.claude_stream import _tokens_of

    tokens = _tokens_of({"input_tokens": 2, "output_tokens": 238,
                         "cache_creation_input_tokens": 0})

    assert "cache_creation_tokens" not in tokens


def test_what_was_written_to_cache_is_carried_off_the_result_event_too():
    """The client-level counterpart of `test_tokens_of_...`: `DEFAULT_ENV`'s
    own recording is the one call of the two fixtures that actually wrote to
    cache rather than reading from it."""
    client, spawn = client_replaying(DEFAULT_ENV)

    collect(client.stream(a_turn()))

    usage = _result(DEFAULT_ENV)["usage"]
    assert client.last_cost["cache_creation_tokens"] == usage["cache_creation_input_tokens"]


def test_a_call_that_says_nothing_about_cost_is_not_billed_the_last_one():
    """
    `last_cost` is read after the stream is drained, so one held over from the
    call before would be emitted again — a run whose sum is its own history.
    """
    processes = [_Process(_lines(MINIMAL)), _Process([])]
    client = ClaudeCodeClient(model=MODEL, spawn=lambda argv, cwd: processes.pop(0))

    collect(client.stream(a_turn()))
    assert client.last_cost

    collect(client.stream(a_turn()))

    assert client.last_cost is None


def test_a_spent_five_hour_window_is_a_refusal_naming_the_reset():
    """
    A `rate_limit_event` carries the reset as an epoch, where `_retry_hint`
    scrapes one out of a sentence with a regex. The run stops blocked: falling
    back to a keyed provider would spend the money this route exists to save,
    and sleeping until the reset is a five-hour sleep inside FreeCAD.
    """
    import time

    client, _ = client_over(_Process(_rate_limited(MINIMAL)))
    resets = [event for event in _events(MINIMAL)
              if event.get("type") == "rate_limit_event"][0]["rate_limit_info"]["resetsAt"]

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    said = str(refusal.value)
    assert MODEL in said
    assert "five-hour" in said
    assert time.strftime("%H:%M", time.localtime(resets)) in said
    assert str(resets) not in said, "an epoch is not a time the user can act on"


def test_an_allowed_rate_limit_event_is_not_a_refusal():
    """One arrives on every call; the recorded one says `allowed`."""
    client, _ = client_replaying()

    assert collect(client.stream(a_turn())).text


def test_a_warned_rate_limit_event_is_not_a_refusal():
    """
    `allowed_warning` is the window filling up, not a window spent: two
    sessions on 13 Sep died blocked on one reporting 26% of the seven-day
    window used.
    """
    client, _ = client_over(_Process(_rate_limited(MINIMAL, status="allowed_warning")))

    assert collect(client.stream(a_turn())).text


def test_a_reply_the_model_was_not_allowed_to_finish_is_a_refusal():
    client, _ = client_over(_Process(_changed(MINIMAL, stop_reason="max_tokens")))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "ran out of output tokens" in str(refusal.value)


def test_a_truncated_call_still_records_what_the_result_event_reported():
    """
    The `result` event that carries `stop_reason: max_tokens` is the same one
    that carries `total_cost_usd` and `usage` -- both belong to one truncated
    call, not to a call that never happened.
    """
    client, _ = client_over(_Process(_changed(MINIMAL, stop_reason="max_tokens")))

    with pytest.raises(ProviderRefused):
        collect(client.stream(a_turn()))

    usage = _result(MINIMAL)["usage"]
    assert client.last_cost == {
        "usd": _result(MINIMAL)["total_cost_usd"], "model": MODEL,
        "prompt_tokens": usage["input_tokens"],
        "completion_tokens": usage["output_tokens"],
        "cached_tokens": usage["cache_read_input_tokens"],
        "resolved_model": next(iter(_result(MINIMAL)["modelUsage"])),
    }


def test_a_truncated_call_with_no_usage_at_all_says_so_rather_than_nothing():
    client, _ = client_over(_Process(_changed(
        MINIMAL, stop_reason="max_tokens", total_cost_usd=None, usage=None)))

    with pytest.raises(ProviderRefused):
        collect(client.stream(a_turn()))

    assert client.last_cost == {"model": MODEL, "usage_unavailable": True}


def test_the_cut_off_reply_does_not_tell_the_user_to_raise_a_cap():
    """
    `modelUsage` reports `maxOutputTokens: 64000` and the CLI takes no flag
    that sets it, so `_truncated`'s "Raise the cap" points at nothing here.
    """
    client, _ = client_over(_Process(_changed(MINIMAL, stop_reason="max_tokens")))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "cap" not in str(refusal.value).lower()


def test_ending_a_run_kills_its_own_call_rather_than_waiting_the_ceiling():
    """
    Stop and Abort (`Mod/ui/daedalus_panel.py :: LoopWorker`) must return in
    seconds against a call under the full `_CEILING`, not only a call given a
    short one, so this proves `Run.end()` itself rather than the timeout.
    """
    client, spawn = client_over(_ProcessThatNeverAnswers())

    done = threading.Event()

    def drive():
        try:
            collect(client.stream(a_turn()))
        except ProviderRefused:
            pass
        finally:
            done.set()

    driver = threading.Thread(target=drive)
    driver.start()
    time.sleep(0.05)

    started = time.monotonic()
    client.run.end()

    assert done.wait(5), "Run.end() did not end the call"
    assert time.monotonic() - started < 5
    assert spawn.process.killed.is_set()
    driver.join(timeout=1)


def test_a_run_ending_with_no_call_in_flight_kills_nothing():
    client = ClaudeCodeClient(model=MODEL, spawn=lambda argv, cwd: None)

    client.run.end()  # must not raise with nothing registered


def _stuck(client):
    """Start a call against *client* that will never return on its own, and its process."""
    started = threading.Event()

    def drive():
        try:
            for chunk in client.stream(a_turn()):
                started.set()
        except ProviderRefused:
            pass

    threading.Thread(target=drive, daemon=True).start()
    while not client.run._live:
        time.sleep(0.01)
    return next(iter(client.run._live))


def test_kill_reaches_a_claude_client_behind_a_role_table():
    from core.llm.claude import kill
    from core.llm.roles import RoleClient

    client, spawn = client_over(_ProcessThatNeverAnswers())
    process = _stuck(client)
    table = RoleClient({"design": client}, caller=client)

    kill(table)

    assert process.killed.is_set()


def test_kill_reaches_a_claude_client_behind_a_profile():
    from core.llm.claude import kill

    class _Profiled:
        def __init__(self, thinking, doing):
            self.thinking = thinking
            self.doing = doing

    thinking, thinking_spawn = client_over(_ProcessThatNeverAnswers())
    doing, doing_spawn = client_over(_ProcessThatNeverAnswers())
    thinking_process = _stuck(thinking)
    doing_process = _stuck(doing)
    profiled = _Profiled(thinking, doing)

    kill(profiled)

    assert thinking_process.killed.is_set()
    assert doing_process.killed.is_set()


def test_kill_of_no_client_is_a_no_op():
    from core.llm.claude import kill

    kill(None)  # a Session with no LLM built yet has nothing to kill


def test_a_call_that_never_answers_ends_the_run_rather_than_hanging_it():
    """
    The CLI has no timeout of its own where the keyed route leans on LiteLLM's,
    and a subprocess that never answers is the parent's problem.
    """
    client, spawn = client_over(_ProcessThatNeverAnswers(), timeout=0.05)

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "did not answer in time" in str(refusal.value)
    assert spawn.process.killed.is_set(), "the process was left running"


def test_the_ceiling_watchdog_is_not_a_thread_python_waits_for_on_the_way_out(monkeypatch):
    """
    The ceiling runs on a thread, and a non-daemon one is joined by the
    interpreter on the way out: a call still in flight when FreeCAD closes
    would hold the process for the rest of the ceiling -- 1500s at `high`. A
    run whose thread was ended from outside never closes the generator that
    cancels it, so the cancel is not the guarantee.
    """
    made = []
    real = threading.Thread

    def record(*args, **kwargs):
        thread = real(*args, **kwargs)
        made.append(thread)
        return thread

    monkeypatch.setattr(threading, "Thread", record)
    client, _ = client_replaying()
    collect(client.stream(a_turn()))

    assert made, "no ceiling was set on the call"
    assert all(thread.daemon for thread in made), "the ceiling would outlive FreeCAD"


def test_a_call_in_flight_while_the_machine_slept_is_over_the_ceiling_on_waking(monkeypatch):
    """
    The monotonic clock stops while macOS sleeps, so a ceiling measured on it
    never fires for a call that spans a sleep: two overnight 203 runs sat 1511s
    and 1877s in one call under a declared 900s. The ceiling reads the wall clock.
    """
    client, spawn = client_over(_ProcessThatNeverAnswers(), timeout=900)
    real = time.time
    reads = []

    def clock():
        reads.append(1)
        return real() + (1000 if len(reads) > 1 else 0)

    monkeypatch.setattr(time, "time", clock)

    started = time.monotonic()
    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert time.monotonic() - started < 5
    assert "did not answer in time" in str(refusal.value)
    assert spawn.process.killed.is_set()


def test_stderr_beyond_the_os_pipe_buffer_does_not_block_the_stream():
    """
    `claude` is started with `--verbose`, so its stderr can carry more than
    the OS pipe buffer (~64 KB). A separate `stderr=PIPE` nobody drains until
    the process exits blocks that write, and the `for line in process.stdout`
    loop this code reads its answer from blocks with it, right up to the
    ceiling. `stderr=subprocess.STDOUT` in `_spawn_claude` folds it into the
    same pipe this loop already reads, so there is nothing left to block on.
    """
    script = (
        "import sys\n"
        "sys.stderr.write('x' * 200000 + chr(10))\n"
        "sys.stderr.flush()\n"
        "print('{\"type\": \"result\", \"subtype\": \"success\", "
        "\"is_error\": false, \"total_cost_usd\": 0.01}')\n"
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8")
    handle.write(script)
    handle.close()

    def spawn(argv, cwd):
        return _spawn_claude([sys.executable, handle.name], cwd)

    client = ClaudeCodeClient(model=MODEL, spawn=spawn, timeout=5.0)

    try:
        started = time.monotonic()
        collect(client.stream(a_turn()))
        elapsed = time.monotonic() - started
    finally:
        os.unlink(handle.name)

    assert elapsed < 5.0


def test_the_clis_plain_text_noise_survives_as_the_error_detail_once_merged():
    """
    stderr was merged into stdout, so `process.stderr` is `None` and no
    longer carries the reason a non-zero exit failed -- the CLI's plain-text
    lines, dropped by `_parsed` as noise, are the only trace of it left.
    """
    class _ProcessWithMergedStderr:
        def __init__(self, lines, returncode):
            self.stdin = _Pipe()
            self.stdout = iter(lines)
            self.stderr = None
            self.returncode = returncode

        def kill(self):
            pass

        def wait(self, timeout=None):
            return self.returncode

    process = _ProcessWithMergedStderr(
        ["Error: invalid API key\n", "some other CLI noise\n"], returncode=1)
    client, _ = client_over(process)

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "invalid API key" in refusal.value.said

def test_the_ceiling_a_call_waits_under_is_the_one_its_own_effort_measured():
    """A call is made at one effort, and the wait it is allowed is that effort's."""
    assert ClaudeCodeClient(MODEL).timeout == _CEILING[effort()]


def test_every_effort_the_dial_offers_leaves_room_for_the_design_call_it_buys():
    """
    The dial buys thinking and the thinking is the wait: 52 s at `low`, 429 s
    at `medium`, 668 s at `high`, on the sheet the recording was taken from.
    Twice each is what a busier sheet than that one has to fit under.
    """
    for effort, ceiling in _CEILING.items():
        assert ceiling >= 2 * _seconds(_design_at(effort)), effort


def test_a_claude_that_exits_without_answering_is_a_refusal():
    client, _ = client_over(_Process([], returncode=1, stderr="Invalid API key"))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "Invalid API key" in refusal.value.said


def test_a_result_event_that_says_it_failed_is_a_refusal():
    client, _ = client_over(_Process(_changed(
        MINIMAL, subtype="error_during_execution", is_error=True)))

    with pytest.raises(ProviderRefused) as refusal:
        collect(client.stream(a_turn()))

    assert "error_during_execution" in str(refusal.value)


def test_no_tool_is_named_the_cli_is_told_to_disable_them_all():
    """
    A named list goes stale the day the CLI adds a tool -- `--tools ""`
    disables every tool the CLI has, named or not, and `--disallowed-tools`
    is gone from the invocation.
    """
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--tools") + 1] == ""
    assert "--disallowed-tools" not in spawn.argv


def test_a_machines_mcp_servers_are_shut_out_by_the_flags_rather_than_by_name():
    """
    The other 44 tools the default environment reports are one machine's MCP
    servers, which have no place in a constant here: `--strict-mcp-config` and
    `--setting-sources ''` leave the recorded run with none.
    """
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert "--strict-mcp-config" in spawn.argv
    assert spawn.argv[spawn.argv.index("--setting-sources") + 1] == ""
    assert _init(MINIMAL)["mcp_servers"] == []


def test_the_preamble_that_flag_set_bought_is_the_one_the_recording_carries():
    """2,385 read against 29,213 created, which is what the flags are for."""
    assert _result(MINIMAL)["usage"]["cache_read_input_tokens"] == 2385
    assert _result(DEFAULT_ENV)["usage"]["cache_creation_input_tokens"] == 29213


def test_the_reply_is_asked_for_as_it_forms_rather_than_when_it_is_finished():
    """
    Measured against `claude` 2.1.232 without the flag: the call succeeds, the
    `result` event carries its cost, and not one `content_block_delta` arrives —
    so the phase reads an empty reply off a call that was paid for. The
    recorded transcript is a stream because it was taken with it.
    """
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert "--include-partial-messages" in spawn.argv


def test_the_call_is_made_at_the_effort_the_user_set(monkeypatch):
    """The dial in the composer is what reaches the CLI, per call."""
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == "high"


def test_an_effort_the_dial_no_longer_offers_falls_back(monkeypatch):
    """
    A level stored by some older build is a flag no route can spell, and the
    CLI answers a bad `--effort` by refusing the call.
    """
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "ultra")
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == _SUBSCRIPTION_EFFORT


def test_the_wait_a_call_is_allowed_moves_with_the_dial(monkeypatch):
    """
    A ceiling that does not move with the effort cuts the answer off rather
    than bounding it: one sheet through Design is 52 s at low and 668 s at
    high.
    """
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "low")
    assert ClaudeCodeClient(MODEL).timeout == _CEILING["low"]

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")
    assert ClaudeCodeClient(MODEL).timeout == _CEILING["high"]


def test_the_subscription_is_the_credential_rather_than_a_key():
    """`--bare` forces ANTHROPIC_API_KEY and breaks subscription auth outright."""
    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert "--safe-mode" in spawn.argv
    assert "--bare" not in spawn.argv
    assert _init(MINIMAL)["apiKeySource"] == "none"


def _design_prompt():
    return "A system prompt longer than a Windows command line. " * 1000


def test_the_system_prompt_reaches_the_process_as_a_file():
    system = _design_prompt()
    client, spawn = client_replaying()

    collect(client.stream(a_turn(system=system)))

    assert spawn.system == system
    assert not any(system[:200] in argument for argument in spawn.argv)


def test_a_design_call_fits_a_windows_command_line():
    """
    Windows caps a whole command line at 32,767 characters and
    a long system prompt is more than that on its own, where `ARG_MAX`
    here is 1,048,576. FreeCAD ships to both.
    """
    system = _design_prompt()
    client, spawn = client_replaying()

    collect(client.stream(a_turn(system=system)))

    assert len(system) > 32767, "the prompt this guards against shrank; pick another"
    assert sum(len(argument) + 1 for argument in spawn.argv) < 32767


def test_the_process_starts_in_a_temporary_directory_of_its_own():
    """No repository's CLAUDE.md sits beside it, so none walks into a prompt."""
    import tempfile

    client, spawn = client_replaying()
    collect(client.stream(a_turn()))

    assert spawn.cwd.startswith(tempfile.gettempdir())
    assert "CLAUDE.md" not in spawn.beside_it


def test_the_system_prompt_file_does_not_outlive_the_call():
    client, spawn = client_replaying()

    collect(client.stream(a_turn()))

    assert not os.path.exists(spawn.argv[spawn.argv.index("--system-prompt-file") + 1])


def test_every_call_starts_in_the_same_directory():
    """The CLI writes its working directory into the prompt, so a new one per call is a cache miss."""
    first_client, first = client_replaying()
    collect(first_client.stream(a_turn()))
    second_client, second = client_replaying()
    collect(second_client.stream(a_turn()))

    assert first.cwd == second.cwd


def test_the_user_message_goes_in_as_one_json_line_on_stdin():
    client, spawn = client_replaying()

    collect(client.stream(a_turn(user="Make a 10mm plate")))

    assert spawn.process.stdin.getvalue().count("\n") == 1
    assert sent(spawn) == {"type": "user", "message": {
        "role": "user", "content": [{"type": "text", "text": "Make a 10mm plate",
                                      "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}}


def test_a_later_turn_carries_the_models_own_earlier_replies_in_order():
    """
    Every earlier assistant reply rides along as a marked text block in the
    one user message stream-json input takes -- the CLI accepts user turns
    only, so a real `assistant` entry is not an option here.
    """
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "Step 1"},
        {"role": "assistant", "content": "box_1()"},
        {"role": "user", "content": "Step 1 failed"},
        {"role": "assistant", "content": "box_1_fixed()"},
        {"role": "user", "content": "Step 2"},
    ]

    turn = _user_turn(messages)["message"]["content"]

    assert [block["text"] for block in turn] == [
        "Step 1", "Your reply:\nbox_1()", "Step 1 failed",
        "Your reply:\nbox_1_fixed()", "Step 2"]


def _drawn(tmp_path, name):
    """What the loop hands the seam for an attachment, whatever its type."""
    path = tmp_path / name
    path.write_bytes(b"a file; whether it decodes is the provider's problem")
    return _as_image(str(path))


def test_a_pdf_goes_out_as_an_anthropic_document_block(tmp_path):
    client, spawn = client_replaying()

    collect(client.stream([{"role": "system", "content": "s"}, {"role": "user", "content": [
        {"type": "text", "text": "Read this"}, _drawn(tmp_path, "bracket.pdf")]}]))

    block = sent(spawn)["message"]["content"][1]
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["source"]["type"] == "base64"
    assert block["source"]["data"]


def test_a_png_goes_out_as_an_anthropic_image_block(tmp_path):
    client, spawn = client_replaying()

    collect(client.stream([{"role": "system", "content": "s"}, {"role": "user", "content": [
        _drawn(tmp_path, "bracket.png")]}]))

    block = sent(spawn)["message"]["content"][0]
    assert block["type"] == "image"
    assert block["source"]["media_type"] == "image/png"


def test_a_jpeg_is_not_labelled_a_png(tmp_path):
    """The mime comes off the file, which is how a JPEG went out as a PNG."""
    client, spawn = client_replaying()

    collect(client.stream([{"role": "system", "content": "s"}, {"role": "user", "content": [
        _drawn(tmp_path, "bracket.jpg")]}]))

    assert sent(spawn)["message"]["content"][0]["source"]["media_type"] == "image/jpeg"


def test_neither_a_pdf_nor_a_png_is_refused_before_the_run_starts(tmp_path):
    """
    LiteLLM has never heard of this provider or its aliases, so the table
    answers None — the benefit of the doubt — and neither is turned away before
    the run starts, which is what the measurement on the ticket says of both.
    """
    drawing = tmp_path / "bracket.pdf"
    drawing.write_bytes(b"%PDF-1.4\n%%EOF\n")
    picture = tmp_path / "bracket.png"
    picture.write_bytes(b"a picture")

    assert unusable_attachments([str(drawing), str(picture)], MODEL) == []


def test_an_alias_is_not_mapped_onto_another_providers_table():
    """
    A `_PREFIX` entry would make LiteLLM's table answer about a different
    model, and a constant saying this provider sees everything goes stale in
    silence.
    """
    assert litellm_model_name(PROVIDER, MODEL) == MODEL
    assert PROVIDER not in _PREFIX


def test_a_key_in_the_shell_does_not_put_the_run_back_on_the_meter(monkeypatch):
    """
    The CLI reaches for `ANTHROPIC_API_KEY` before the subscription's own
    OAuth, and a run that finds one is billed by the token under a label that
    says subscription.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-a-shell-profile")

    assert "ANTHROPIC_API_KEY" not in _environment()
    assert _environment().get("PATH") == os.environ.get("PATH")


def test_the_binary_is_taken_from_the_environment_first(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "/somewhere/of/my/own/claude")

    assert find_claude() == "/somewhere/of/my/own/claude"


def test_a_known_path_answers_when_the_environment_says_nothing(monkeypatch, tmp_path):
    installed = tmp_path / "claude"
    installed.write_text("")
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.setattr(llm_claude, "_CLAUDE_CANDIDATES", (str(installed),))

    assert find_claude() == str(installed)


def test_the_path_is_the_last_thing_asked(monkeypatch):
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.setattr(llm_claude, "_CLAUDE_CANDIDATES", ())
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/" + name)

    assert find_claude() == "/opt/homebrew/bin/claude"


def test_a_machine_with_no_claude_on_it_says_what_to_point_where(monkeypatch):
    """
    `env -i PATH=/usr/bin:/bin sh -c 'command -v claude'` finds nothing, and
    that is the PATH a Finder-launched FreeCAD inherits.
    """
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.setattr(llm_claude, "_CLAUDE_CANDIDATES", ())
    monkeypatch.setattr(shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError) as complaint:
        find_claude()

    assert "CLAUDE_CMD" in str(complaint.value)


def _auth(payload, returncode=0, stderr=""):
    def run(argv, **kwargs):
        run.argv = list(argv)
        return subprocess.CompletedProcess(argv, returncode, json.dumps(payload), stderr)

    return run


SIGNED_IN = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
             "email": "someone@example.com", "subscriptionType": "max"}


def test_the_test_button_reports_the_account_and_the_plan(monkeypatch):
    """
    Every other provider's Test proves the key by fetching the model list. The
    proof that matches a provider with no key is which account is signed in,
    and `claude auth status` spends no call at all.
    """
    monkeypatch.setenv("CLAUDE_CMD", "/nowhere/claude")
    asked = _auth(SIGNED_IN)

    ok, said = signed_in(run=asked)

    assert ok is True
    assert "someone@example.com" in said
    assert "max" in said
    assert "claude.ai" in said, "a CLI holding an API key would read the same"
    assert asked.argv == ["/nowhere/claude", "auth", "status", "--json"]


def test_a_cli_that_is_not_signed_in_says_how_to_sign_in(monkeypatch):
    monkeypatch.setenv("CLAUDE_CMD", "/nowhere/claude")

    ok, said = signed_in(run=_auth({"loggedIn": False}))

    assert ok is False
    assert "claude auth login" in said


def test_no_claude_at_all_is_reported_rather_than_raised(monkeypatch):
    monkeypatch.delenv("CLAUDE_CMD", raising=False)
    monkeypatch.setattr(llm_claude, "_CLAUDE_CANDIDATES", ())
    monkeypatch.setattr(shutil, "which", lambda _: None)

    ok, said = signed_in(run=_auth(SIGNED_IN))

    assert ok is False
    assert "CLAUDE_CMD" in said


def test_the_model_the_cli_answered_with_is_recorded_beside_the_alias_asked_for():
    client, _ = client_over(_Process(_changed(
        MINIMAL, modelUsage={"claude-opus-5-5": {"costUSD": 0.1}})))

    collect(client.stream(a_turn()))

    assert client.last_cost["model"] == MODEL
    assert client.last_cost["resolved_model"] == "claude-opus-5-5"


def test_a_call_silent_for_the_stall_limit_is_cut_with_a_stall_not_left_to_the_ceiling():
    client, spawn = client_over(_ProcessThatNeverAnswers(), timeout=900, stall=0.05)

    started = time.monotonic()
    with pytest.raises(CallStalled):
        collect(client.stream(a_turn()))

    assert time.monotonic() - started < 5
    assert spawn.process.killed.is_set()


def test_the_ceiling_is_a_stall_too_so_one_retry_policy_covers_both():
    client, _ = client_over(_ProcessThatNeverAnswers(), timeout=0.05)

    with pytest.raises(CallStalled) as stalled:
        collect(client.stream(a_turn()))

    assert "did not answer in time" in str(stalled.value)


def test_a_call_that_keeps_streaming_is_not_cut_however_long_it_runs_past_the_stall_limit():
    class Trickle:
        def __init__(self, lines):
            self.lines = iter(lines)

        def __iter__(self):
            return self

        def __next__(self):
            time.sleep(0.04)
            return next(self.lines)

    process = _Process(_lines(MINIMAL))
    process.stdout = Trickle(_lines(MINIMAL))
    client, _ = client_over(process, timeout=900, stall=0.3)

    assert collect(client.stream(a_turn())).text
    assert not process.killed
