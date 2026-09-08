"""
Level 1 — the two-profile dial: `thinking` for Design and Plan, `doing` for
Execute, Assertion and Review.

`profile_of` is the vocabulary and `ProfiledClient` is the seam
`core/loop.py :: _one_call` asks `for_phase` of. What is checked here is the
map itself, the client answering each phase from the right profile, the
ceiling following each profile's own effort rather than one dial shared by
both, and the backward-compatible read of a settings store that has only ever
held the old single model and effort.
"""
import pytest

from core.llm import (
    ClaudeCodeClient,
    ProfiledClient,
    ScriptedLLMClient,
    profile_of,
    effort_for_profile,
)
from core.llm.effort_policy import _CEILING, _SUBSCRIPTION_EFFORT

MODEL = "sonnet"


@pytest.mark.parametrize("phase, profile", [
    ("design", "thinking"),
    ("plan", "thinking"),
    ("execute", "doing"),
    ("assertion", "doing"),
    ("review", "doing"),
])
def test_every_phase_has_exactly_one_profile(phase, profile):
    assert profile_of(phase) == profile


def test_a_phase_this_map_has_never_heard_of_is_the_cheaper_guess():
    assert profile_of("something-new") == "doing"


def test_for_phase_answers_from_the_profile_that_owns_the_phase():
    thinking = ScriptedLLMClient(["design reply"])
    doing = ScriptedLLMClient(["execute reply"])
    client = ProfiledClient(thinking=thinking, doing=doing)

    assert client.for_phase("design") is thinking
    assert client.for_phase("plan") is thinking
    assert client.for_phase("execute") is doing
    assert client.for_phase("assertion") is doing
    assert client.for_phase("review") is doing


def test_the_ceiling_follows_each_profiles_own_effort_and_not_one_global_dial():
    thinking = ClaudeCodeClient(MODEL, effort="low")
    doing = ClaudeCodeClient(MODEL, effort="high")

    assert thinking.timeout == _CEILING["low"]
    assert doing.timeout == _CEILING["high"]
    assert thinking.timeout != doing.timeout


def test_an_explicit_effort_reaches_the_cli_flag_per_client():
    from test_llm_claude_code import client_replaying, a_turn
    from conftest import collect

    client, spawn = client_replaying(effort="low")
    collect(client.stream(a_turn()))

    assert spawn.argv[spawn.argv.index("--effort") + 1] == "low"


def test_an_explicit_effort_wins_over_whatever_the_dial_is_set_to(monkeypatch):
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")

    assert ClaudeCodeClient(MODEL, effort="low").timeout == _CEILING["low"]


def test_no_explicit_effort_still_reads_the_global_dial(monkeypatch):
    from core import settings

    monkeypatch.setattr(settings.instance(), "get_effort", lambda: "high")

    assert ClaudeCodeClient(MODEL).timeout == _CEILING["high"]


@pytest.fixture
def clean_settings():
    from utils import settings_manager

    sm = settings_manager.instance()
    grp = sm._grp()
    keys = ("LastUsedModel", "DefaultModel", "SelectedModel", "Effort",
            "ProfileModel:thinking", "ProfileModel:doing",
            "ProfileEffort:thinking", "ProfileEffort:doing")
    saved = {key: grp.GetString(key, "") for key in keys}
    for key in keys:
        grp.SetString(key, "")
    yield sm
    for key, value in saved.items():
        grp.SetString(key, value)


def test_a_user_with_only_the_old_single_model_gets_both_profiles_set_to_it(
        clean_settings):
    clean_settings.set_last_model("gpt-5.6-sol")

    assert clean_settings.get_profile_model("thinking") == "gpt-5.6-sol"
    assert clean_settings.get_profile_model("doing") == "gpt-5.6-sol"


def test_a_user_with_only_the_old_single_effort_gets_both_profiles_set_to_it(
        clean_settings):
    clean_settings.set_effort("high")

    assert effort_for_profile("thinking") == "high"
    assert effort_for_profile("doing") == "high"


def test_setting_one_profile_leaves_the_other_on_the_old_single_setting(
        clean_settings):
    clean_settings.set_last_model("gpt-5.6-sol")
    clean_settings.set_profile_model("doing", "haiku")

    assert clean_settings.get_profile_model("doing") == "haiku"
    assert clean_settings.get_profile_model("thinking") == "gpt-5.6-sol"


def test_a_user_with_neither_setting_still_gets_a_usable_effort(clean_settings):
    assert effort_for_profile("thinking") == _SUBSCRIPTION_EFFORT
    assert effort_for_profile("doing") == _SUBSCRIPTION_EFFORT
