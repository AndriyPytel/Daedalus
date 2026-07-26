# Execute may ask the Document before it writes the code

Five sessions of logs, twelve failed attempts, and the tool counter reads: 18 calls to `apply_step`, zero to anything else. `describe_object`, `supported_types` and `dependencies` had no caller outside the tests. The mechanism ADR-0006 chose against hallucinated API — ask the running FreeCAD — was unreachable from Execute, the one phase that hallucinates: it replied with code in a single shot, from the Step description and a Snapshot, and every question it might have had it answered by guessing. `supported_types` would have killed `PartDesign::Sketch` in one call; instead that invention cost two whole Steps in two different sessions.

Execute now gets a bounded turn or two to look first. It may reply `{"look": {"tool": ..., "arguments": ...}}`; the loop runs that Tool, appends the answer to the request and asks again, at most `looks_per_step` times, then offers nothing to look with — which is how "answer now" is said in a protocol whose only vocabulary is the request.

## This is not a group chat (ADR-0003)

The distinction ADR-0003 draws is between a fixed pipeline and agents negotiating with each other. Nothing here negotiates: the phases and their order are unchanged, Execute talks to no other phase, and the loop still decides when a Step runs. What changed is that one phase may read the Document before answering — which Plan and Review already do, via the Snapshot they are handed. Execute was the odd one out.

`apply_step` is deliberately not lookable. Execute proposes a Step; the loop applies it. A phase that could apply its own code would be a second, unbudgeted route to the Document, and *that* would be the group chat.

## Why the JSON protocol and not native function-calling

Provider-native tool calls would mean accumulating `tool_calls` deltas per provider in `core/llm.py`, and local models — a supported target (ADR-0004) — are the weakest at it. All three phases already speak one dialect: reply with JSON, the loop parses it. A `look` is one more shape in that dialect, and it cost no change to the LLM client at all.

## Consequences

- A Step costs up to `looks_per_step + 1` completions instead of one. Two is enough to check a type and a property list; more is stalling, not thinking.
- A look at a Tool that does not exist, or one that raises `ToolError`, comes back as `{"error": ...}` in the next request. A bad question is a thing to correct, not a reason to lose the Step.
- Looks are emitted as `TOOL_CALL`/`TOOL_RESULT`, so the panel shows what the assistant asked, not just what it ran.
- The registry is now genuinely the only route to the Document, in fact and not only in intent — the read-only Tools finally have a production caller.
