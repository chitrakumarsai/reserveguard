"""Show why a rate is what it is, preview an adjustment, and give a seller its own view."""

import pytest

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.config import Settings
from reserveguard.models import RiskKind, RiskSignal, Tier
from reserveguard.reserve_engine import MAX_RATE_BPS, compute_rate_bps, explain_rate
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    SAMPLE_EVIDENCE_PATH,
    build_demo_service,
)

pytestmark = pytest.mark.unit

OFFLINE = Settings(use_llm=False)


def _signal(kind: RiskKind, weight: int, ids: tuple[str, ...], clears: RiskKind | None = None):  # type: ignore[no-untyped-def]
    return RiskSignal(kind=kind, weight_bps=weight, evidence_ids=ids, clears=clears)


CLUSTER = _signal(RiskKind.COMPLAINT_CLUSTER, 1500, ("ev-1", "ev-2"))
TRACKING = _signal(RiskKind.TRACKING_FAILURE, 500, ("ev-3",))
DELIVERED = _signal(RiskKind.DELIVERY_CONFIRMED, 0, ("ev-9",), RiskKind.COMPLAINT_CLUSTER)


def test_explanation_adds_up_to_the_rate() -> None:
    explanation = explain_rate(Tier.NEW, [CLUSTER, TRACKING])

    assert explanation.base_bps == 1500
    assert [(line.kind, line.weight_bps, line.status) for line in explanation.lines] == [
        (RiskKind.COMPLAINT_CLUSTER, 1500, "active"),
        (RiskKind.TRACKING_FAILURE, 500, "active"),
    ]
    assert explanation.rate_bps == 3500
    assert explanation.capped is False


def test_cleared_signals_are_shown_but_not_counted() -> None:
    explanation = explain_rate(Tier.NEW, [CLUSTER, TRACKING, DELIVERED])

    statuses = {line.kind: line.status for line in explanation.lines}
    assert statuses[RiskKind.COMPLAINT_CLUSTER] == "cleared"
    assert statuses[RiskKind.DELIVERY_CONFIRMED] == "clears"
    assert explanation.lines[0].cleared_by == ("ev-9",)
    assert explanation.rate_bps == 2000


def test_cap_is_reported() -> None:
    explanation = explain_rate(Tier.NEW, [_signal(RiskKind.COMPLAINT, 1500, ("e",))] * 3)

    assert explanation.uncapped_bps == 6000
    assert explanation.rate_bps == MAX_RATE_BPS
    assert explanation.capped is True


@pytest.mark.parametrize(
    "signals",
    [[], [CLUSTER], [CLUSTER, TRACKING, DELIVERED], [DELIVERED, CLUSTER], [TRACKING] * 9],
)
def test_explanation_always_matches_compute_rate(signals: list[RiskSignal]) -> None:
    for tier in Tier:
        assert explain_rate(tier, signals).rate_bps == compute_rate_bps(tier, signals)


def test_preview_shows_the_charge_without_moving_money() -> None:
    service, fake = build_demo_service()
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), OFFLINE))
    wallet = fake.get_balance("acct_seller_c")

    preview = service.preview("C")

    assert preview.plan.charge_cents == 80_000
    assert preview.account_id == "acct_seller_c"
    assert preview.wallet_cents == wallet
    assert "35.00%" in preview.memo
    assert fake.get_balance("acct_seller_c") == wallet  # nothing moved
    assert service.approve("C").plan == preview.plan  # approve does what was previewed


def test_preview_without_a_pending_decision_raises() -> None:
    service, _ = build_demo_service()
    with pytest.raises(LookupError):
        service.preview("A")


def test_statement_lists_only_that_sellers_money() -> None:
    service, fake = build_demo_service()
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), OFFLINE))
    service.approve("C")
    service.assess("C", classify_evidence(DELIVERY_CONFIRMATIONS, OFFLINE))

    statement = service.statement("C")

    assert {e.seller_id for e in statement.entries} == {"C"}
    kinds = [e.kind.value for e in statement.entries]
    assert kinds[:4] == ["gross", "fee", "reserve_hold", "payout"]
    assert "charge" in kinds and "release" in kinds
    assert statement.totals.held_cents == 80_000
    assert statement.wallet_cents == fake.get_balance("acct_seller_c")
    assert statement.rate_bps == 2000
