"""
Every `/v1` route but `/v1/health` demands the one configured bearer token: no token, a wrong token or another scheme is 401 problem+json with `WWW-Authenticate: Bearer`, and the right token reaches the route as the configured owner.

The route list is read from the app, so a route added later is covered without editing this test.
"""
import re

import pytest
from fastapi.testclient import TestClient

from service import owner as owner_module
from service.app import app
from service.owner import Authenticator, authenticator, current_owner

TOKEN = "t" * 40


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    monkeypatch.setenv("DAEDALUS_OWNER_ID", "usr_alice")
    return TestClient(app)


def routes():
    return [(method.upper(), re.sub(r"\{[^}]+\}", "x", path))
            for path, item in app.openapi()["paths"].items() if path.startswith("/v1") for method in sorted(item)]


def refused(response):
    assert response.status_code == 401, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["status"] == 401


def test_the_route_list_is_not_empty_and_has_the_open_health_route():
    assert ("GET", "/v1/health") in routes() and len(routes()) > 10


def test_every_route_but_health_refuses_a_request_without_a_valid_token(client):
    for method, path in routes():
        if path == "/v1/health":
            continue
        refused(client.request(method, path))
        refused(client.request(method, path, headers={"Authorization": "Bearer " + "x" * 40}))
        refused(client.request(method, path, headers={"Authorization": "Basic " + TOKEN}))
        refused(client.request(method, path, headers={"Authorization": TOKEN}))


def test_health_needs_no_token(client):
    assert client.get("/v1/health").status_code == 200


def test_the_right_token_is_accepted_on_a_route(client):
    assert client.get("/v1/runs/run_missing", headers={"Authorization": "Bearer " + TOKEN}).status_code != 401


def test_the_scheme_name_is_case_insensitive(client):
    assert client.get("/v1/users/usr_alice/preferences", headers={"Authorization": "bearer " + TOKEN}).status_code != 401


def test_current_owner_hands_the_bearer_token_to_the_authenticator():
    seen = []

    class Recording(Authenticator):
        def authenticate(self, token):
            seen.append(token)
            return "usr_bob"

    assert current_owner(authorization="Bearer abc", auth=Recording()) == "usr_bob"
    assert seen == ["abc"]


def test_the_bearer_token_implementation_returns_its_owner_for_its_token_only():
    assert owner_module.BearerToken(TOKEN, "usr_alice").authenticate(TOKEN) == "usr_alice"
    assert owner_module.BearerToken(TOKEN, "usr_alice").authenticate("other") is None


def test_a_route_asks_the_authenticator_interface_and_not_the_token(client):
    class Fixed(Authenticator):
        def authenticate(self, token):
            return "usr_alice" if token == "letmein" else None

    app.dependency_overrides[authenticator] = Fixed
    try:
        assert client.get("/v1/users/usr_alice/preferences", headers={"Authorization": "Bearer letmein"}).status_code != 401
        refused(client.get("/v1/users/usr_alice/preferences", headers={"Authorization": "Bearer " + TOKEN}))
    finally:
        app.dependency_overrides.clear()


def test_tokens_are_compared_in_constant_time(monkeypatch):
    calls = []
    real = owner_module.hmac.compare_digest
    monkeypatch.setattr(owner_module.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert owner_module.BearerToken(TOKEN, "usr_alice").authenticate("wrong") is None
    assert owner_module.BearerToken(TOKEN, "usr_alice").authenticate(TOKEN) == "usr_alice"
    assert len(calls) == 2


def test_a_token_with_non_ascii_text_is_refused_not_a_server_error(client):
    refused(client.get("/v1/runs", headers={"Authorization": "Bearer t\u00e9".encode("latin-1")}))


def test_a_gateway_with_no_token_configured_answers_503_problem_json_and_never_lets_a_request_in(monkeypatch):
    monkeypatch.delenv("DAEDALUS_TOKEN", raising=False)
    answer = TestClient(app).get("/v1/runs", headers={"Authorization": "Bearer anything"})
    assert answer.status_code == 503 and answer.headers["content-type"] == "application/problem+json"
    monkeypatch.setenv("DAEDALUS_TOKEN", "")
    assert TestClient(app).get("/v1/runs", headers={"Authorization": "Bearer "}).status_code == 503
