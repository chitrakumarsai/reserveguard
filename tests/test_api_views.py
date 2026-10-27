"""API read views: why a rate is what it is, what approve would do, a seller's statement."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from reserveguard.agent.evidence import load_evidence
from reserveguard.airwallex.client import AirwallexTransientError
from reserveguard.api import create_app
from reserveguard.config import Settings
from reserveguard.scenario import DEMO_GROSS_CENTS, SAMPLE_EVIDENCE_PATH

pytestmark = pytest.mark.integration

TOKEN = "t"  # noqa: S105 - test fixture, not a credential
RISK_EVIDENCE = [e.model_dump(mode="json") for e in load_evidence(SAMPLE_EVIDENCE_PATH)]


@pytest.fixture
def client() -> TestClient:
    settings = Settings(use_fake_airwallex=True, use_llm=False, reserveguard_api_token=TOKEN)
    api = TestClient(create_app(settings), headers={"Authorization": f"Bearer {TOKEN}"})
    api.post("/settle", json={"cycle_id": "c1", "gross_cents": DEMO_GROSS_CENTS})
    return api


def _get(client: TestClient, path: str) -> Any:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def test_explain_breaks_the_rate_down(client: TestClient) -> None:
    client.post("/sellers/C/assess", json={"evidence": RISK_EVIDENCE})

    body = _get(client, "/sellers/C/explain")

    assert body["current_rate_bps"] == 1500
    assert body["pending"] is True
    assert body["base_bps"] == 1500
    assert [(line["kind"], line["weight_bps"]) for line in body["lines"]] == [
        ("complaint_cluster", 1500),
        ("tracking_failure", 500),
    ]
    assert body["rate_bps"] == 3500
    assert body["capped"] is False


def test_pending_previews_the_charge_and_moves_nothing(client: TestClient) -> None:
    client.post("/sellers/C/assess", json={"evidence": RISK_EVIDENCE})
    before = _get(client, "/sellers/C/statement")["wallet_cents"]

    body = _get(client, "/sellers/C/pending")

    assert body["plan"]["charge_cents"] == 80_000
    assert body["account_id"] == "acct_seller_c"
    assert body["wallet_cents"] == before
    assert "35.00%" in body["memo"]
    assert _get(client, "/sellers/C/statement")["wallet_cents"] == before


def test_pending_without_a_decision_is_404(client: TestClient) -> None:
    assert client.get("/sellers/A/pending").status_code == 404


def test_statement_is_the_sellers_own_view(client: TestClient) -> None:
    body = _get(client, "/sellers/A/statement")

    assert body["seller_id"] == "A"
    assert body["account_id"] == "acct_seller_a"
    assert body["held_cents"] == 15_000
    assert body["paid_out_cents"] == 255_000
    assert {e["kind"] for e in body["entries"]} >= {"gross", "fee", "reserve_hold", "payout"}
    assert all(e["seller_id"] == "A" for e in body["entries"])
    assert body["wallet_cents"] == 255_000


@pytest.mark.parametrize("view", ["explain", "pending", "statement"])
def test_views_reject_unknown_sellers_and_anonymous_callers(client: TestClient, view: str) -> None:
    assert client.get(f"/sellers/Z/{view}").status_code == 404
    assert client.get(f"/sellers/A/{view}", headers={"Authorization": ""}).status_code == 401


@pytest.mark.parametrize(("view", "method"), [("pending", "preview"), ("statement", "statement")])
def test_views_map_provider_errors_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, view: str, method: str
) -> None:
    def fail(*_args: object) -> None:
        raise AirwallexTransientError("sandbox down")

    monkeypatch.setattr(client.app.state.service, method, fail)  # type: ignore[attr-defined]

    response = client.get(f"/sellers/C/{view}")

    assert response.status_code == 502
    assert "sandbox down" not in response.text
