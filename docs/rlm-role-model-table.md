# RLM role-to-model table

One row per role, settled here so FCC-294's baseline and FCC-319's 24-run
comparison can be costed against concrete provider/model/effort values
instead of a guess per ticket. This is a document, not code: FCC-312 wires
these values into Preferences and `core/llm.py`.

Follows the pattern already used by `myga.yml`'s `default` / `worker` /
`runner` / `expert` model classes -- a small, named set of provider+model
pairs that every role picks from, rather than one model per ticket.

| Role             | Provider  | Model                  | Effort |
|------------------|-----------|-------------------------|--------|
| orchestrator     | Anthropic | claude-sonnet-5         | low    |
| Design           | Anthropic | claude-opus-5           | high   |
| Plan             | Anthropic | claude-opus-5           | high   |
| Execute          | Anthropic | claude-sonnet-5         | medium |
| Assertion        | Anthropic | claude-sonnet-5         | medium |
| Review           | Anthropic | claude-sonnet-5         | medium |
| sub-agent worker | Anthropic | claude-haiku-4-5        | low    |

Design and Plan get the highest-effort model: turning a drawing into a Step
list is the reasoning-heavy end of the run, the same split `core/llm.py`'s
existing `thinking` profile already draws. Execute, Assertion and Review
already have the Step in hand and get the mid model at medium effort, the
same split `core/llm.py`'s existing `doing` profile draws. The orchestrator
costs one call the user waits on before Design starts, so it stays on the
cheaper model at low effort rather than the high end. A sub-agent worker is
depth-1, bounded, and mechanical by construction (it cannot itself spawn),
so it gets the cheapest model in the table.

## Fallback rule

A role this table has never heard of falls back to **the caller's own
model**, not to a cheaper default. This is the epic's settled rule
(`FCC-291`, "Model and effort are chosen per role"): an unnamed role
inherits whatever provider/model/effort the agent that spawned it was
already running under, so a new role introduced later never silently
downgrades until someone adds it a row here.
