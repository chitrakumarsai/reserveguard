"""API: re-rate one seller on new evidence, then approve the pending decision."""

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from reserveguard.agent.evidence import load_evidence
from reserveguard.airwallex.client import AirwallexTransientError
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.api import ASSESS_RATE_LIMIT, MAX_EVIDENCE_TEXT, RateLimiter, create_app
from reserveguard.config import Settings
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    SAMPLE_EVIDENCE_PATH,
    payment_waiter,
)
from reserveguard.settlement import PaymentNotSettledError

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


def _held(client: TestClient, seller_id: str) -> int:
    seller = next(s for s in client.get("/sellers").json() if s["seller_id"] == seller_id)
    return int(seller["held_cents"])


def test_assess_holds_a_large_increase_for_approval(client: TestClient) -> None:
    response = _assess(client, "C", RISK_EVIDENCE)

    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["new_rate_bps"] == 3500
    assert body["decision"]["needs_approval"] is True
    assert body["memo"] is None
    assert body["classifier"] == "keywords"
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
    assert _held(client, "C") == 140_000
    assert all(c["passed"] for c in client.get("/reconciliation").json())


def test_releases_through_the_api_wait_for_approval(client: TestClient) -> None:
    _assess(client, "C", RISK_EVIDENCE)
    client.post("/sellers/C/approve")

    body = _assess(client, "C", DELIVERIES).json()

    assert body["decision"]["new_rate_bps"] == 2000
    assert body["decision"]["needs_approval"] is True
    assert body["memo"] is None
    assert _held(client, "C") == 140_000  # nothing released by evidence alone
    approved = client.post("/sellers/C/approve").json()
    assert approved["plan"]["release_cents"] == 60_000
    assert _held(client, "C") == 80_000
    assert all(c["passed"] for c in client.get("/reconciliation").json())


def test_approve_without_a_pending_decision_is_a_conflict(client: TestClient) -> None:
    assert client.post("/sellers/A/approve").status_code == 409


def test_unknown_seller_is_not_found(client: TestClient) -> None:
    assert _assess(client, "Z", RISK_EVIDENCE).status_code == 404
    assert client.post("/sellers/Z/approve").status_code == 404
    settle = client.post("/settle", json={"cycle_id": "c9", "gross_cents": {"Z": 1}})
    assert settle.status_code == 404


def test_assess_rejects_empty_or_oversized_evidence(client: TestClient) -> None:
    assert _assess(client, "C", []).status_code == 422
    huge = [{**RISK_EVIDENCE[0], "text": "x" * (MAX_EVIDENCE_TEXT + 1)}]
    assert _assess(client, "C", huge).status_code == 422


def test_assess_is_rate_limited(client: TestClient) -> None:
    statuses = [_assess(client, "A", DELIVERIES).status_code for _ in range(ASSESS_RATE_LIMIT)]
    assert set(statuses) == {200}
    assert _assess(client, "A", DELIVERIES).status_code == 429


def test_rate_limit_frees_up_after_the_window() -> None:
    now = [0.0]
    limiter = RateLimiter(limit=1, window_s=60.0, clock=lambda: now[0])
    limiter.check()
    with pytest.raises(HTTPException):
        limiter.check()

    now[0] = 60.0

    limiter.check()  # the first call has aged out


@pytest.mark.parametrize("method", ["assess_detailed", "approve", "settle_cycle"])
def test_unsettled_payment_is_a_502_that_says_whether_money_moved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise PaymentNotSettledError("ch_1", "PENDING", ledger_applied=True)

    monkeypatch.setattr(client.app.state.service, method, fail)  # type: ignore[attr-defined]
    calls = {
        "assess_detailed": lambda: _assess(client, "C", RISK_EVIDENCE),
        "approve": lambda: client.post("/sellers/C/approve"),
        "settle_cycle": lambda: client.post(
            "/settle", json={"cycle_id": "c2", "gross_cents": {"A": 1}}
        ),
    }

    response = calls[method]()

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "error": "payment_not_settled",
        "payment_id": "ch_1",
        "status": "PENDING",
        "ledger_applied": True,
    }


@pytest.mark.parametrize("method", ["assess_detailed", "approve", "reconciliation"])
def test_provider_errors_map_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AirwallexTransientError("sandbox down")

    monkeypatch.setattr(client.app.state.service, method, fail)  # type: ignore[attr-defined]
    calls = {
        "assess_detailed": lambda: _assess(client, "C", RISK_EVIDENCE),
        "approve": lambda: client.post("/sellers/C/approve"),
        "reconciliation": lambda: client.get("/reconciliation"),
    }

    response = calls[method]()

    assert response.status_code == 502
    assert "sandbox down" not in response.text  # provider details stay server-side


def test_live_client_polls_airwallex_until_final() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    live = RestAirwallexClient(settings, sleep=lambda _s: None, clock=lambda: 100.0)
    fake = FakeAirwallexClient({None: 100})
    transfer = fake.transfer_to_seller(None, 1, "USD", "r", "k")

    assert payment_waiter(fake)(transfer, 5.0) == "SETTLED"
    with pytest.raises(AirwallexTransientError):  # zero budget: one look, then give up
        live.get_status = lambda _id: "PENDING"  # type: ignore[method-assign]
        payment_waiter(live)("tr_1", 0.0)


def test_reserve_endpoints_require_the_token(client: TestClient) -> None:
    anonymous = {"Authorization": ""}
    assert client.post("/sellers/C/approve", headers=anonymous).status_code == 401
    response = client.post("/sellers/C/assess", json={"evidence": DELIVERIES}, headers=anonymous)
    assert response.status_code == 401
