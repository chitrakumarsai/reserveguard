"""API: re-rate one seller on new evidence, then approve the pending decision."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from reserveguard.agent.evidence import load_evidence
from reserveguard.airwallex.client import AirwallexTransientError
from reserveguard.api import create_app
from reserveguard.config import Settings
from reserveguard.scenario import DELIVERY_CONFIRMATIONS, DEMO_GROSS_CENTS, SAMPLE_EVIDENCE_PATH

pytestmark = pytest.mark.integration

TOKEN = "t"  # noqa: S105 - test fixture, not a credential
RISK_EVIDENCE = [e.model_dump(mode="json") for e in load_evidence(SAMPLE_EVIDENCE_PATH)]
DELIVERIES = [e.model_dump(mode="json") for e in DELIVERY_CONFIRMATIONS]


@pytest.fixture
def client() -> TestClient:
    settings = Settings(use_fake_airwallex=True, use_llm=False, reserveguard_api_token=TOKEN)
    api = TestClient(create_app(settings), headers={"Authorization": f"Bearer {TOKEN}"})
    api.post("/settle", json={"cycle_id": "c1", "gross_cents": DEMO_GROSS_CENTS})
    return api


def _assess(client: TestClient, seller_id: str, evidence: list[dict[str, Any]]) -> Any:
    return client.post(f"/sellers/{seller_id}/assess", json={"evidence": evidence})


def test_assess_holds_a_large_increase_for_approval(client: TestClient) -> None:
    response = _assess(client, "C", RISK_EVIDENCE)

    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["new_rate_bps"] == 3500
    assert body["decision"]["needs_approval"] is True
    assert body["memo"] is None
    assert {s["kind"] for s in body["signals"]} == {"complaint_cluster", "tracking_failure"}
    rates = {s["seller_id"]: s["rate_bps"] for s in client.get("/sellers").json()}
    assert rates["C"] == 1500  # unchanged until approved


def test_approve_applies_the_pending_charge(client: TestClient) -> None:
    _assess(client, "C", RISK_EVIDENCE)

    response = client.post("/sellers/C/approve")

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["charge_cents"] == 80_000
    assert "35.00%" in body["memo"]
    seller_c = next(s for s in client.get("/sellers").json() if s["seller_id"] == "C")
    assert seller_c["rate_bps"] == 3500
    assert seller_c["held_cents"] == 140_000
    assert all(c["passed"] for c in client.get("/reconciliation").json())


def test_assess_auto_applies_a_decrease_and_returns_its_memo(client: TestClient) -> None:
    _assess(client, "C", RISK_EVIDENCE)
    client.post("/sellers/C/approve")

    body = _assess(client, "C", DELIVERIES).json()

    assert body["decision"]["needs_approval"] is False
    assert body["decision"]["new_rate_bps"] == 2000
    assert "Auto-applied" in body["memo"]
    assert all(c["passed"] for c in client.get("/reconciliation").json())


def test_approve_without_a_pending_decision_is_a_conflict(client: TestClient) -> None:
    assert client.post("/sellers/A/approve").status_code == 409


def test_unknown_seller_is_not_found(client: TestClient) -> None:
    assert _assess(client, "Z", RISK_EVIDENCE).status_code == 404
    assert client.post("/sellers/Z/approve").status_code == 404


def test_assess_rejects_empty_evidence(client: TestClient) -> None:
    assert _assess(client, "C", []).status_code == 422


@pytest.mark.parametrize("method", ["assess", "approve"])
def test_provider_errors_map_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    def fail(*_args: object) -> None:
        raise AirwallexTransientError("sandbox down")

    service = client.app.state.service  # type: ignore[attr-defined]
    monkeypatch.setattr(service, method, fail)

    response = (
        _assess(client, "C", RISK_EVIDENCE)
        if method == "assess"
        else client.post("/sellers/C/approve")
    )

    assert response.status_code == 502
    assert "sandbox down" not in response.text  # provider details stay server-side


def test_reserve_endpoints_require_the_token(client: TestClient) -> None:
    anonymous = {"Authorization": ""}
    assert client.post("/sellers/C/approve", headers=anonymous).status_code == 401
    response = client.post("/sellers/C/assess", json={"evidence": DELIVERIES}, headers=anonymous)
    assert response.status_code == 401
