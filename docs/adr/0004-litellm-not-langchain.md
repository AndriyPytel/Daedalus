# LiteLLM for provider access; not LangChain, not a single OpenAI-compatible client

The addon installs its dependencies into FreeCAD's embedded interpreter, a sandbox shared with every other addon, so dependency weight is a real cost. LangChain is an orchestration framework and we would use roughly the chat model and `bind_tools` — importing an architecture to avoid writing a client. LiteLLM targets exactly the stated need, unified provider access without orchestration, in one package.

## Considered Options

- **LangChain** (the previous stack, six packages) — rejected: framework where a client was needed, and it lags on provider-specific features.
- **One `openai` SDK against OpenAI-compatible endpoints everywhere** — rejected on Anthropic's own documentation: the compatibility layer supports neither prompt caching nor structured outputs, and while `thinking` can be enabled it does not return the reasoning content. Both matter here — the UI shows the assistant thinking, and every Step resends Document state, which without caching is paid for twice. This route survives only for local models and OpenRouter.
- **Native SDKs plus a thin adapter** — the fallback if LiteLLM turns out not to pass Anthropic thinking blocks and `cache_control` through faithfully. Unifying layers leak precisely where providers differ most, so this is worth proving before building on it.
