# Plan → Execute → Review is a fixed pipeline, not a multi-agent group chat

This project takes its inspiration from MEDA (ASME IDETC-CIE 2025), so a reader may expect its shape: seven agents in an AutoGen `GroupChat` with `speaker_selection_method="auto"` and `max_round=50`. We deliberately did not copy that. Most of those agents exist there to do work that is deterministic here — execution, checking whether execution succeeded, and reading model state are code, not conversations — and LLM-chosen routing gives no exit conditions and no honest progress to show the user.

## Consequences

- Three phases remain, each for a different reason: Plan because the user must approve it, Execute because it needs a narrow context, Review because self-assessment is worthless.
- Review is given the Intent and the outcome but never the Plan or the code. An agent that can see its own reasoning defends it.
- Termination is explicit — a budget of attempts per Step and of Review cycles per Plan — after which the assistant stops and asks the user rather than looping or quietly giving up.
- Specialist sub-agents remain reachable later: the loop is a function of `(Intent, context)` and all Document access goes through Tools, so narrowing either is configuration, not a rewrite.
