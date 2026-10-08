"""
`/v1/users/{id}/preferences` GET and PATCH over real Postgres, and the three levels that resolve into a Run's settings: server defaults, then the owner's preferences, then the Run's own `overrides`.

A fake worker echoes the settings it is sent, so what a Run runs with is read off the channel. A provider key or base URL is a server secret, so naming one in a preference or an override is 422.
"""
import json

import pytest
from fastapi.testclient import TestClient

from service import db
from service.app import app
from service.owner import authenticator, Authenticator
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import FAKE, final

TOKEN = "t" * 40
HEADERS = {"Authorization": "Bearer " + TOKEN}
DEFAULTS = {"auto_approve_plan": True, "interactive": False, "confirm": False, "budget_usd": 5.0}
SECRETS = ({"api_keys": [{"provider": "Anthropic", "api_key": "sk-x"}]}, {"base_urls": {"Ollama": "http://x"}},
           {"api_key": "sk-x"}, {"base_url": "http://x"}, {"anthropic_api_key": "sk-x"},
           {"role_table": [{"role": "design", "provider": "Anthropic", "model": "m", "api_key": "sk-x"}]},
           {"default_model": {"provider": "Anthropic", "model": "m", "base_url": "http://x"}})


@pytest.fixture
def api(database_url, monkeypatch):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    monkeypatch.setenv("DAEDALUS_OWNER_ID", "usr_alice")
    monkeypatch.setenv("DAEDALUS_MAX_SESSIONS", "100")
    with TestClient(app, headers=HEADERS) as client:
        yield client


def preferences(api, owner="usr_alice"):
    return api.get("/v1/users/%s/preferences" % owner)


def settings_of(api, **body):
    run = api.post("/v1/runs", json={"prompt": "settings", **body})
    assert run.status_code == 202, run.text
    final(api, run.json()["id"])
    with db.connect(api.app.state.pool._url) as check:
        row = check.execute("SELECT payload FROM events WHERE run_id = %s AND type = 'settings'", (run.json()["id"],)).fetchone()
    assert row[0]["token_leaked"] is False
    return row[0]["settings"]


def test_a_new_owner_has_no_stored_preferences_and_the_server_defaults_are_effective(api):
    body = preferences(api).json()
    assert body["object"] == "preferences" and body["user_id"] == "usr_alice"
    assert body["preferences"] == {} and body["effective"] == DEFAULTS


def test_patching_a_preference_is_stored_merged_and_read_back(api):
    first = api.patch("/v1/users/usr_alice/preferences", json={"effort": "high", "budget_usd": 2.5})
    assert first.status_code == 200 and first.json()["preferences"] == {"effort": "high", "budget_usd": 2.5}
    api.patch("/v1/users/usr_alice/preferences", json={"effort": "low"})
    stored = preferences(api).json()
    assert stored["preferences"] == {"effort": "low", "budget_usd": 2.5}
    assert stored["effective"] == {**DEFAULTS, "effort": "low", "budget_usd": 2.5}


def test_patching_null_removes_a_preference_and_the_default_comes_back(api):
    api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": 2.5})
    removed = api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": None}).json()
    assert removed["preferences"] == {} and removed["effective"]["budget_usd"] == 5.0


def test_every_preference_the_ticket_names_can_be_stored(api):
    table = [{"role": "design", "provider": "Anthropic", "model": "claude-x", "effort": "high"}]
    body = {"role_table": table, "default_model": {"provider": "Anthropic", "model": "claude-x"},
            "naming_model": {"provider": "Anthropic", "model": "claude-y"}, "effort": "medium",
            "auto_approve_plan": False, "interactive": True, "confirm": True, "budget_usd": 1.0}
    assert api.patch("/v1/users/usr_alice/preferences", json=body).json()["preferences"] == body


def test_a_patched_preference_changes_the_next_runs_settings(api):
    assert settings_of(api)["budget_usd"] == 5.0
    api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": 2.5, "effort": "high"})
    assert settings_of(api) == {**DEFAULTS, "budget_usd": 2.5, "effort": "high"}


def test_a_per_run_override_wins_over_the_preference_and_only_for_that_run(api):
    api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": 2.5, "interactive": True})
    assert settings_of(api, overrides={"budget_usd": 0.5})["budget_usd"] == 0.5
    assert settings_of(api, overrides={"interactive": False})["interactive"] is False
    assert settings_of(api)["budget_usd"] == 2.5 and settings_of(api)["interactive"] is True


def test_the_server_defaults_come_from_the_environment_and_a_preference_beats_them(api, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DEFAULTS", json.dumps({"budget_usd": 9.0, "default_model": {"provider": "Anthropic", "model": "m"}}))
    assert settings_of(api)["budget_usd"] == 9.0 and settings_of(api)["default_model"]["model"] == "m"
    api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": 3.0})
    assert settings_of(api)["budget_usd"] == 3.0


@pytest.mark.parametrize("body", SECRETS)
def test_a_provider_key_or_base_url_in_a_preference_is_422_problem_json_and_nothing_is_stored(api, body):
    refused = api.patch("/v1/users/usr_alice/preferences", json={"effort": "high", **body})
    assert refused.status_code == 422 and refused.headers["content-type"] == "application/problem+json"
    assert preferences(api).json()["preferences"] == {}


@pytest.mark.parametrize("body", SECRETS)
def test_a_provider_key_or_base_url_in_a_per_run_override_is_422_and_starts_no_run(api, body):
    refused = api.post("/v1/runs", json={"prompt": "hello", "overrides": body})
    assert refused.status_code == 422 and refused.headers["content-type"] == "application/problem+json"
    assert api.get("/v1/runs").json()["data"] == []


def test_a_malformed_preference_is_422(api):
    for body in ({"budget_usd": -1}, {"budget_usd": "lots"}, {"interactive": "maybe"}, {"effort": 3}, {"default_model": "m"}):
        assert api.patch("/v1/users/usr_alice/preferences", json=body).status_code == 422, body


def test_preferences_are_stored_per_owner_and_one_owner_never_reads_anothers(api, repo):
    api.patch("/v1/users/usr_alice/preferences", json={"budget_usd": 2.5})
    other = repo.create_user()
    repo.set_preference(other, "budget_usd", 7.0)
    assert repo.list_preferences("usr_alice") == {"budget_usd": 2.5}
    assert repo.list_preferences(other) == {"budget_usd": 7.0}
    app.dependency_overrides[authenticator] = lambda: type("Bob", (Authenticator,), {"authenticate": lambda self, token: other})()
    try:
        assert api.get("/v1/users/%s/preferences" % other).json()["preferences"] == {"budget_usd": 7.0}
        assert settings_of(api)["budget_usd"] == 7.0
    finally:
        app.dependency_overrides.clear()


def test_a_user_id_other_than_the_callers_is_404_problem_json_for_get_and_patch(api, repo):
    other = repo.create_user()
    for call in (lambda: preferences(api, other), lambda: api.patch("/v1/users/%s/preferences" % other, json={"effort": "low"}),
                 lambda: preferences(api, "usr_nobody")):
        refused = call()
        assert refused.status_code == 404 and refused.headers["content-type"] == "application/problem+json"
    assert repo.list_preferences(other) == {}


def flags_of(api, **body):
    run = api.post("/v1/runs", json={"prompt": "flags", **body}).json()
    final(api, run["id"])
    with db.connect(api.app.state.pool._url) as check:
        sent = check.execute("SELECT payload FROM events WHERE run_id = %s AND type = 'flags'", (run["id"],)).fetchone()[0]
    return run, sent


def test_interactive_and_confirm_resolve_defaults_then_preferences_then_the_runs_own_field_and_the_worker_is_sent_the_result(api):
    run, sent = flags_of(api)
    assert (run["interactive"], run["confirm"]) == (False, False) and sent == {"interactive": False, "confirm": False}
    api.patch("/v1/users/usr_alice/preferences", json={"interactive": True, "confirm": True})
    run, sent = flags_of(api)
    assert (run["interactive"], run["confirm"]) == (True, True) and sent == {"interactive": True, "confirm": True}
    run, sent = flags_of(api, interactive=False)
    assert (run["interactive"], run["confirm"]) == (False, True) and sent == {"interactive": False, "confirm": True}
    run, sent = flags_of(api, overrides={"confirm": False})
    assert (run["interactive"], run["confirm"]) == (True, False) and sent == {"interactive": True, "confirm": False}


def test_the_run_stores_what_it_resolved_so_a_later_preference_change_does_not_rewrite_it(api):
    api.patch("/v1/users/usr_alice/preferences", json={"interactive": True})
    run, _ = flags_of(api)
    api.patch("/v1/users/usr_alice/preferences", json={"interactive": False})
    assert api.get("/v1/runs/%s" % run["id"]).json()["interactive"] is True
