"""
Role-table defaults for settings persistence.

This module exists so `settings.py` can seed an empty role table without
importing `core/llm` at all.
"""

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
