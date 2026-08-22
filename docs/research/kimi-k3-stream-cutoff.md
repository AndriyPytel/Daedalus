# Kimi K3 (OpenCode Go) SSE stream ends mid-reasoning: candidate causes

Context: `litellm.completion(stream=True, model="openai/kimi-k3", api_base=<OpenCode Go
gateway>, reasoning_effort="medium", max_tokens=32768, stream_options={"include_usage": True})`
receives many `delta.reasoning_content` chunks, then the SSE stream ends with no content
delta, no `finish_reason`, no exception. `opencode` itself (CLI/TUI/desktop), talking to the
*same* gateway (`https://opencode.ai/zen/go/v1`), reportedly does not visibly break.

Primary-source research (GitHub issues/PRs, source diffs) points at one dominant cause and
several contributing/interacting factors. Ranked by likelihood.

## 1. (Most likely, root cause) The OpenCode Go gateway itself does a server-side idle/stall
   cutoff on long-reasoning generations — this is a known, currently-open, reproduced-with-raw-HTTP bug in the gateway, not a litellm bug.

- **Evidence**: opencode issue [anomalyco/opencode#44148](https://github.com/anomalyco/opencode/issues/44148)
  — "[Go plan] Empty completions (finish_reason=other) and mid-stream cutoffs on
  long-reasoning requests — reproduced via direct API calls across multiple models" (open).
  Reproduced with **plain HTTPS from Node.js, no opencode client, no litellm** against
  `https://opencode.ai/zen/go/v1/chat/completions`: 11 of 12 consecutive long-reasoning
  (30-110s) probes failed with either (a) a complete stream + finish chunk but
  `finish_reason: "other"` and 0 content, or (b) **a clean FIN with no finish chunk and no
  `[DONE]`** — i.e. exactly "stream just ends, no finish_reason, no exception" from the
  client's point of view. Short prompts on the same connections succeeded 3/3. The issue
  author's conclusion: server-side idle/stall timeout between the Zen gateway and the model
  backend, intermittent and time-of-day dependent (works some hours, ~90% failure other
  hours) — which is consistent with "other harnesses don't reliably see it either, they were
  just luckier or they retry."
- **Model-specific corroboration**: [anomalyco/opencode#44203](https://github.com/anomalyco/opencode/issues/44203)
  "Kimi K3 and Ox Alpha - Response Cut off" (closed, but symptom matches exactly, same
  gateway/model as this task).
- **One-line test**: re-run the same prompt several times at different times of day directly
  against the gateway with `curl -N` (bypass litellm entirely) and see if the clean-FIN cutoff
  reproduces without any client in the loop — if yes, this is not fixable client-side, only
  retry-around-able.

## 2. (High confidence, explains why opencode "doesn't break") opencode's own client added
   an idle-stream watchdog + automatic retry-on-stall/retry-on-truncation; litellm has none.

- **Evidence**: [anomalyco/opencode#40010](https://github.com/anomalyco/opencode/pull/40010)
  "fix(provider): recover stalled model streams" — adds a provider-neutral idle watchdog for
  streaming responses (adaptive 15-30 min default, or explicit `chunkTimeout`), which converts
  a stall into "a typed retryable provider error" that goes through opencode's existing retry
  policy. Closes anomalyco/opencode#37580.
- [anomalyco/opencode#39473](https://github.com/anomalyco/opencode/pull/39473) "fix: retry
  truncated provider streams" — when the AI SDK synthesizes an `other`/`unknown` finish
  because the provider stream ended with no finish reason (exactly this scenario), opencode
  now treats it as a retryable stream error instead of a completed empty result, and retried
  4x over ~24h in testing. Closes anomalyco/opencode#37852.
- [anomalyco/opencode#38541](https://github.com/anomalyco/opencode/pull/38541) "fix(llm):
  prevent infinite loop when finish_reason missing in stream" — defaults a missing
  `finish_reason` to `"stop"` rather than hanging, another defensive layer around the same
  gateway behavior.
- **Concrete client-side change to test**: litellm.completion() has no built-in "retry if the
  stream ended with 0 content/no finish_reason" behavior — you have to add it yourself: wrap
  the stream consumption, detect "chunks received but stream closed with no
  `finish_reason` and no accumulated `content`", and re-issue the request (with backoff) when
  that happens. This is the single highest-leverage client-side fix available, since cause #1
  is server-side and not otherwise controllable.

## 3. (Plausible, needs live verification) litellm's underlying transport may raise or swallow
   the resulting connection-level signal differently depending on transport in use.

- **Evidence**: [BerriAI/litellm#33222](https://github.com/BerriAI/litellm/pull/33222) "fix(streaming):
  surface upstream connection resets instead of empty 200 streams" — documents the exact
  general failure class (a downstream TCP RST/FIN mid-SSE-stream with no terminal chunk was
  previously swallowed by litellm as a normal 200 stream end, with no exception raised).
  Confirmed via source diff that the fix (aiohttp transport's exception map turning
  `ClientPayloadError`/`ServerDisconnectedError` into `httpx.ReadError`) is present in litellm
  `v1.95.0` (tag committed 2026-08-02, fix merged 2026-07-16, verified by fetching
  `litellm/llms/custom_httpx/aiohttp_transport.py` at tag `v1.95.0` directly from GitHub raw).
  **Caveat**: this fix lives in litellm's own `aiohttp_transport.py` / `streaming_handler.py`.
  For `model="openai/<id>"` with a custom `api_base`, litellm may route the low-level HTTP call
  through the vendored `openai` Python SDK's own `httpx` client rather than litellm's aiohttp
  transport — in that code path this fix would not apply, and the swallow could still occur via
  the OpenAI SDK's own `Stream` iterator treating a clean-FIN as a normal end-of-iteration.
  Related aiohttp-transport idle-timeout note: [BerriAI/litellm#22747](https://github.com/BerriAI/litellm/issues/22747)
  documents a **hardcoded ~60s aiohttp socket idle timeout that ignores the user's `timeout`
  param** for long-reasoning (GPT-5-Pro) requests — a distinct but related failure mode if
  litellm *is* on the aiohttp path for this call.
- **One-line test**: enable litellm debug logging (`litellm._turn_on_debug()` or
  `LITELLM_LOG=DEBUG`) around a failing call and check whether an `httpx.ReadError` /
  `ServerDisconnectedError` is logged-and-swallowed at some layer, vs. genuinely no
  network-level signal at all (which would point back to cause #1's clean-FIN, since a clean
  FIN followed by nothing further looks identical to "stream ended normally" at the transport
  level unless the code explicitly checks "did I ever see a finish_reason/[DONE]").

## 4. (Lower priority — investigated, ruled mostly out) `reasoning_content`-only chunks being
   silently dropped by litellm as "empty" before reaching the caller.

- **Evidence**: [BerriAI/litellm#28000](https://github.com/BerriAI/litellm/issues/28000) /
  [#28012](https://github.com/BerriAI/litellm/pull/28012) "Gemini streaming + thinking —
  reasoning_content always None (thought chunks silently dropped in `is_chunk_non_empty`)".
  This is a real, well-documented bug class, but reading litellm's `v1.95.0` source directly
  (`litellm/litellm_core_utils/streaming_handler.py`) shows the plain OpenAI-compatible branch
  (`custom_llm_provider="openai"`, used by `model="openai/kimi-k3"`) already copies
  `reasoning_content` from `original_chunk.choices[0].delta` into `model_response.choices[0].delta`
  *before* `is_chunk_non_empty()` runs, and `is_chunk_non_empty()` already checks
  `model_response.choices[0].delta.reasoning_content is not None`. The #28000/#28012 bug was
  specific to the Vertex/Gemini custom-provider streaming path, not the generic OpenAI path —
  so this particular class of bug is unlikely to be silently truncating chunks in this setup,
  though it is worth a quick empirical check.
- **One-line test**: log every raw SSE line from the gateway alongside litellm's parsed chunks
  for one failing run; if a `reasoning_content`-bearing line arrives after the last chunk
  litellm surfaced, this cause is confirmed and #28012's fix pattern (make sure reasoning-only
  deltas are never classified "empty") is the template for a workaround/monkeypatch.

## 5. (Speculative, no direct evidence found) A specific request param needed for this gateway.

- No litellm or opencode issue found tying `max_tokens`, `stream_options.include_usage`, or
  `allowed_openai_params` specifically to Kimi K2/K3 truncation on this gateway. The `max_tokens: 32000`
  value appears unchanged/unremarkable in the opencode repro in #44148 (same value, same
  symptom, with or without a coding client). No evidence that omitting `max_tokens` or
  `stream_options` avoids the cutoff — the opencode issue reproduces the same failure with
  opencode's own request shape.
- **One-line test (low priority, only if #1-#3 don't explain it)**: A/B one run with
  `stream_options` omitted and one with it present, controlling for time-of-day (given #44148's
  finding that failure rate is highly time-dependent, any small-N A/B here is likely to be
  noise — don't over-interpret a handful of successes/failures).

## Summary recommendation

Cause #1 (server-side idle/stall cutoff in the OpenCode Go gateway itself, currently open and
unfixed) is the dominant explanation, and it is out of the client's control. Cause #2 (opencode's
own client has retry-on-stall/retry-on-truncated-stream logic that litellm plumbing does not)
is very likely why other harnesses "don't visibly break" — they are hitting the same gateway
bug but papering over it with retries. The actionable, high-leverage client-side change is to
add the same class of mitigation opencode uses: detect a stream that ended with reasoning
content but no `finish_reason` and no accumulated `content`, and retry the request.
