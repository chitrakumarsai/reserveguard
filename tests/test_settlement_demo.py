"""Integration test: the full demo scenario against FakeAirwallexClient."""

import pytest
from fastapi.testclient import TestClient

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.agent.tools import ReadOnlyTools, draft_memo
from reserveguard.airwallex.client import (
    AirwallexTransientError,
    InsufficientFundsError,
)
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.api import create_app
from reserveguard.config import Settings
from reserveguard.models import RiskKind, Seller, Tier
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    SAMPLE_EVIDENCE_PATH,
    build_demo_service,
)
from reserveguard.settlement import MAX_ATTEMPTS, SettlementService

pytestmark = pytest.mark.integration


def _all_pass(service: SettlementService) -> bool:
    return all(r.passed for r in service.reconciliation())


@pytest.fixture
def settled() -> tuple[SettlementService, FakeAirwallexClient]:
    service, fake = build_demo_service()
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="2026-10-cycle-1")
    return service, fake


def test_settlement_table(settled: tuple[SettlementService, FakeAirwallexClient]) -> None:
    service, fake = settled
    lines = {line.seller_id: line for line in service.last_settlement}
    expected = {
        "A": (300_000, 30_000, 500, 15_000, 255_000),
        "B": (300_000, 30_000, 1000, 30_000, 240_000),
        "C": (400_000, 40_000, 1000, 40_000, 320_000),
    }
    for seller_id, (gross, fee, rate, reserve, payout) in expected.items():
        line = lines[seller_id]
        assert (line.gross_cents, line.fee_cents, line.rate_bps) == (gross, fee, rate)
        assert (line.reserve_cents, line.payout_cents) == (reserve, payout)
    assert sum(line.reserve_cents for line in lines.values()) == 85_000
    assert sum(line.payout_cents for line in lines.values()) == 815_000
    assert fake.get_balance(None) == 185_000
    assert _all_pass(service)


def test_full_scenario_for_seller_c(
    settled: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, fake = settled
    a_before, b_before = service.ledger.totals("A"), service.ledger.totals("B")

    # Step 1: evidence -> 30% rate, needs approval
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))
    decision = service.assess("C", signals)
    assert decision.new_rate_bps == 3000
    assert decision.target_reserve_cents == 120_000
    assert decision.needs_approval is True
    assert set(decision.evidence_ids) == {
        "ev-c-001",
        "ev-c-002",
        "ev-c-003",
        "ev-c-004",
        "ev-c-005",
    }
    assert service.ledger.totals("C").held_cents == 40_000  # nothing moved yet
    assert _all_pass(service)

    # Step 2: seller withdrew most funds; approve -> charge $500, debt $300
    fake.withdraw("acct_seller_c", fake.get_balance("acct_seller_c") - 50_000)
    result = service.approve("C")
    assert (result.plan.charge_cents, result.plan.debt_cents) == (50_000, 30_000)
    c = service.ledger.totals("C")
    assert (c.held_cents, c.debt_cents) == (90_000, 30_000)
    assert fake.get_balance("acct_seller_c") == 0
    assert service.ledger.totals("A") == a_before
    assert service.ledger.totals("B") == b_before
    assert _all_pass(service)

    # Step 3: deliveries confirmed -> 15%, cancel debt, release $300
    confirmed = classify_evidence(DELIVERY_CONFIRMATIONS, Settings(use_llm=False))
    assert [s.kind for s in confirmed] == [RiskKind.DELIVERY_CONFIRMED]
    decision = service.assess("C", confirmed)
    assert decision.new_rate_bps == 1500
    assert decision.target_reserve_cents == 60_000
    assert decision.needs_approval is False
    c = service.ledger.totals("C")
    assert (c.held_cents, c.debt_cents, c.released_cents) == (60_000, 0, 30_000)
    assert c.net_paid_cents == 270_000
    assert c.net_paid_cents + c.released_cents + c.held_cents == 360_000
    assert fake.get_balance("acct_seller_c") == 30_000
    assert _all_pass(service)

    memos = service.memos("C")
    assert len(memos) == 2
    assert "ev-c-001" in memos[0]


def test_approve_without_pending_raises(
    settled: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = settled
    with pytest.raises(LookupError):
        service.approve("A")


def test_unknown_seller_is_rejected(
    settled: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = settled
    with pytest.raises(KeyError):
        service.assess("Z", [])


def test_transfer_is_retried_with_same_idempotency_key() -> None:
    service, fake = build_demo_service()
    fake.fail_next(1)
    service.settle_cycle({"A": 300_000}, cycle_id="c1")
    keys = fake.calls
    assert len(keys) == 2 and keys[0] == keys[1]
    assert fake.get_balance("acct_seller_a") == 255_000


def test_transient_errors_eventually_propagate() -> None:
    service, fake = build_demo_service()
    fake.fail_next(10)
    with pytest.raises(AirwallexTransientError):
        service.settle_cycle({"A": 300_000}, cycle_id="c1")


def test_fake_idempotency_and_guards() -> None:
    fake = FakeAirwallexClient({None: 1_000, "s": 100})
    first = fake.transfer_to_seller("s", 500, "USD", "r", "k1")
    again = fake.transfer_to_seller("s", 500, "USD", "r", "k1")
    assert first == again
    assert fake.get_balance("s") == 600
    assert fake.get_status(first) == "SETTLED"
    with pytest.raises(InsufficientFundsError):
        fake.charge_seller("s", 601, "USD", "r", "k2")
    with pytest.raises(InsufficientFundsError):
        fake.transfer_to_seller("s", 10_000, "USD", "r", "k3")
    with pytest.raises(ValueError):
        fake.transfer_to_seller("s", 0, "USD", "r", "k4")
    with pytest.raises(KeyError):
        fake.get_status("nope")
    with pytest.raises(InsufficientFundsError):
        fake.withdraw("s", 10_000)


def test_read_only_tools(settled: tuple[SettlementService, FakeAirwallexClient]) -> None:
    service, _ = settled
    tools = ReadOnlyTools(service)
    summary = tools.seller_summary("C")
    assert summary["held_cents"] == 40_000
    assert {s["seller_id"] for s in tools.list_sellers()} == {"A", "B", "C"}
    assert not hasattr(tools, "transfer_to_seller")


def test_draft_memo_mentions_rates_and_evidence() -> None:
    service, _ = build_demo_service()
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="2026-10-cycle-1")
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))
    memo = draft_memo(service.assess("C", signals))
    assert "10.00%" in memo and "30.00%" in memo and "approval" in memo


def test_api_endpoints() -> None:
    client = TestClient(create_app(Settings(use_fake_airwallex=True)))
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/sellers").json()) == 3
    settle = client.post("/settle", json={"cycle_id": "c1", "gross_cents": DEMO_GROSS_CENTS})
    assert settle.status_code == 200
    assert sum(line["payout_cents"] for line in settle.json()) == 815_000
    checks = client.get("/reconciliation").json()
    assert checks and all(c["passed"] for c in checks)
    bad = client.post("/settle", json={"cycle_id": "c2", "gross_cents": {"Z": 100}})
    assert bad.status_code == 404
    negative = client.post("/settle", json={"cycle_id": "c3", "gross_cents": {"A": -1}})
    assert negative.status_code == 422


def test_api_refuses_real_client_until_implemented() -> None:
    with pytest.raises(RuntimeError, match="RestAirwallexClient"):
        create_app(Settings(use_fake_airwallex=False))


def test_replaying_a_cycle_never_pays_twice() -> None:
    service, fake = build_demo_service()
    first = service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="cycle-1")
    again = service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="cycle-1")
    assert first == again
    assert fake.get_balance(None) == 185_000
    assert _all_pass(service)


def test_failed_cycle_can_be_resumed_without_double_paying_earlier_sellers() -> None:
    service, fake = build_demo_service()
    fake.fail_next(MAX_ATTEMPTS)  # the first payout exhausts its retries
    with pytest.raises(AirwallexTransientError):
        service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="cycle-1")
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="cycle-1")
    assert fake.get_balance("acct_seller_a") == 255_000
    assert _all_pass(service)


def test_reassessing_same_evidence_is_a_no_op(
    settled: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = settled
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))
    first = service.assess("C", signals)
    second = service.assess("C", signals)
    assert first.new_rate_bps == second.new_rate_bps == 3000


def test_settling_again_drops_stale_pending_decision(
    settled: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = settled
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))
    service.assess("C", signals)
    service.settle_cycle({"C": 100_000}, cycle_id="cycle-2")
    with pytest.raises(LookupError):
        service.approve("C")


def test_duplicate_sellers_are_rejected() -> None:
    seller = Seller("A", "A", Tier.NEW, "acct_shared")
    other = Seller("B", "B", Tier.NEW, "acct_shared")
    with pytest.raises(ValueError, match="account_id"):
        SettlementService(FakeAirwallexClient({}), [seller, other])


def test_api_maps_provider_errors_to_502() -> None:
    client = TestClient(create_app(Settings(use_fake_airwallex=True)))
    response = client.post("/settle", json={"cycle_id": "big", "gross_cents": {"A": 10**9}})
    assert response.status_code == 502


def test_default_base_url_is_airwallex_sandbox() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.airwallex_base_url == "https://api.sandbox.airwallex.com"
