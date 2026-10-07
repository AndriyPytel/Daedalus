from fastapi.testclient import TestClient

from service.app import app


def test_health_under_v1_returns_200():
    assert TestClient(app).get("/v1/health").status_code == 200


def test_openapi_lists_the_health_route():
    document = TestClient(app).get("/openapi.json").json()
    assert "/v1/health" in document["paths"]
