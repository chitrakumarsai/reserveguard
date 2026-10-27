"""The ops console: a static page over the same authenticated API."""

import pytest
from fastapi.testclient import TestClient

from reserveguard.api import create_app
from reserveguard.config import Settings

pytestmark = pytest.mark.integration

TOKEN = "console-test-token-7f3a"  # noqa: S105 - test fixture, not a credential


@pytest.fixture
def client() -> TestClient:
    settings = Settings(use_fake_airwallex=True, use_llm=False, reserveguard_api_token=TOKEN)
    return TestClient(create_app(settings))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_console_page_is_served_with_a_strict_csp(client: TestClient) -> None:
    response = client.get("/console")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]  # no inline scripts
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "<script" in response.text and "console.js" in response.text
    assert TOKEN not in response.text


@pytest.mark.parametrize("asset", ["console.js", "console.css"])
def test_console_assets_are_served(client: TestClient, asset: str) -> None:
    response = client.get(f"/console/static/{asset}")
    assert response.status_code == 200
    assert len(response.text) > 100


def test_console_page_scripts_never_write_html_from_data(client: TestClient) -> None:
    script = client.get("/console/static/console.js").text
    assert "innerHTML" not in script
    assert "eval(" not in script


def test_info_reports_mode_without_secrets(client: TestClient) -> None:
    body = client.get("/info", headers=_auth()).json()

    assert body == {"airwallex": "fake", "classifier": "keywords", "llm_model": None}
    assert client.get("/info").status_code == 401


def test_samples_endpoint_returns_both_evidence_sets(client: TestClient) -> None:
    body = client.get("/samples/evidence", headers=_auth()).json()

    assert {e["id"] for e in body["risk"]} >= {"ev-c-001", "ev-c-005"}
    assert all(e["id"].startswith("ev-c-1") for e in body["deliveries"])
    assert client.get("/samples/evidence").status_code == 401
