"""A seller's reserve debt is collected from their next payouts, never from anyone else's."""

import pytest

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.config import Settings
from reserveguard.models import EntryKind
from reserveguard.scenario import DEMO_GROSS_CENTS, SAMPLE_EVIDENCE_PATH, build_demo_service
from reserveguard.settlement import SettlementService

pytestmark = pytest.mark.integration


@pytest.fixture
def in_debt() -> tuple[SettlementService, FakeAirwallexClient]:
    """Demo steps 1-2: C is re-rated to 35%, can only cover $500, and owes $300."""
    service, fake = build_demo_service()
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="cycle-1")
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))
    service.assess("C", signals)
    fake.withdraw("acct_seller_c", fake.get_balance("acct_seller_c") - 50_000)
    service.approve("C")
    assert service.ledger.totals("C").debt_cents == 30_000
    return service, fake


def _all_pass(service: SettlementService) -> bool:
    return all(r.passed for r in service.reconciliation())


def test_payout_larger_than_debt_clears_it(
    in_debt: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, fake = in_debt
    a_before = service.ledger.totals("A")
    wallet_before = fake.get_balance("acct_seller_c")

    # $1,000 gross at 35%: fee $100, reserve $350, payout $550, of which $300 repays the debt.
    (line,) = service.settle_cycle({"C": 100_000}, cycle_id="cycle-2")

    assert (line.reserve_cents, line.debt_collected_cents, line.payout_cents) == (
        35_000,
        30_000,
        25_000,
    )
    c = service.ledger.totals("C")
    assert c.debt_cents == 0
    assert c.held_cents == 110_000 + 35_000 + 30_000  # back at the 35% target of $5,000
    assert fake.get_balance("acct_seller_c") == wallet_before + 25_000
    assert service.ledger.totals("A") == a_before
    assert _all_pass(service)


def test_payout_smaller_than_debt_is_kept_whole(
    in_debt: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, fake = in_debt
    wallet_before = fake.get_balance("acct_seller_c")

    # $400 gross at 35%: fee $40, reserve $140, payout $220, all of it kept against the debt.
    (line,) = service.settle_cycle({"C": 40_000}, cycle_id="cycle-2")

    assert (line.debt_collected_cents, line.payout_cents, line.transfer_id) == (22_000, 0, None)
    assert service.ledger.totals("C").debt_cents == 8_000
    assert fake.get_balance("acct_seller_c") == wallet_before
    assert _all_pass(service)


def test_collection_is_recorded_on_the_sellers_own_ledger(
    in_debt: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = in_debt

    service.settle_cycle({"A": 100_000, "C": 100_000}, cycle_id="cycle-2")

    collected = [e for e in service.ledger.entries if e.kind is EntryKind.DEBT_COLLECTED]
    assert [(e.seller_id, e.amount_cents, e.reference) for e in collected] == [
        ("C", 30_000, "settle:cycle-2:C")
    ]


def test_replaying_a_cycle_does_not_collect_twice(
    in_debt: tuple[SettlementService, FakeAirwallexClient],
) -> None:
    service, _ = in_debt
    first = service.settle_cycle({"C": 40_000}, cycle_id="cycle-2")

    again = service.settle_cycle({"C": 40_000}, cycle_id="cycle-2")

    assert again == first
    assert service.ledger.totals("C").debt_cents == 8_000
    assert _all_pass(service)
