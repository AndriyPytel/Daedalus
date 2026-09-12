"""
Level 1 -- `tools/run.py`'s model seam: the provider named on the command line
is the one the client is built for.

`run.py` is a fresh process with no panel behind it, so nothing has fetched a
model list and `core.model_registry` is empty; without the provider being
named, every model resolves to nothing at all.
"""
import os
import sys

from core.llm import ClaudeCodeClient
from core import settings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402


def test_a_gateway_model_builds_that_gateways_client(the_settings_never_reach_the_user_s_freecad):
    settings.instance().set_api_keys([{"provider": "OpenCode Go", "api_key": "sk-zen"}])

    client = run.client_for("OpenCode Go", "glm-5.3-flash", "medium")

    assert client.model == "openai/glm-5.3-flash"
    assert client.api_base == "https://opencode.ai/zen/go/v1"
    assert client.extra_headers["x-opencode-session"]


def test_the_subscription_is_still_answered_by_its_own_client(
        the_settings_never_reach_the_user_s_freecad):
    assert isinstance(run.client_for("Claude (subscription)", "opus", "medium"),
                      ClaudeCodeClient)
