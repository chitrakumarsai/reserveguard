"""Unit tests for the pure reserve maths."""

import pytest

from reserveguard.models import RiskKind, RiskSignal, Tier
from reserveguard.reserve_engine import (
    MAX_RATE_BPS,
    base_rate_bps,
    compute_rate_bps,
    plan_adjustment,
    propose_reserve_change,
    settle_gross,
)

pytestmark = pytest.mark.unit


def _signal(kind: RiskKind, weight: int = 500, clears: RiskKind | None = None) -> RiskSignal:
    return RiskSignal(kind=kind, weight_bps=weight, evidence_ids=("ev-1",), clears=clears)


CLUSTER = _signal(RiskKind.COMPLAINT_CLUSTER, 1500)
TRACKING = _signal(RiskKind.TRACKING_FAILURE)
DELIVERED = _signal(RiskKind.DELIVERY_CONFIRMED, 0, clears=RiskKind.COMPLAINT_CLUSTER)


@pytest.mark.parametrize(
    ("tier", "expected"),
    [(Tier.TRUSTED, 500), (Tier.STANDARD, 1000), (Tier.NEW, 1000)],
)
def test_base_rate_by_tier(tier: Tier, expected: int) -> None:
    assert base_rate_bps(tier) == expected


def test_rate_adds_signal_weights() -> None:
    assert compute_rate_bps(Tier.NEW, [CLUSTER, TRACKING]) == 3000


def test_delivery_confirmed_removes_points_of_cleared_signal() -> None:
    assert compute_rate_bps(Tier.NEW, [CLUSTER, TRACKING, DELIVERED]) == 1500


def test_clearing_signal_without_target_changes_nothing() -> None:
    assert compute_rate_bps(Tier.NEW, [TRACKING, DELIVERED]) == 1500


def test_rate_is_capped() -> None:
    signals = [_signal(RiskKind.COMPLAINT, 1500)] * 5
    assert compute_rate_bps(Tier.NEW, signals) == MAX_RATE_BPS


@pytest.mark.parametrize(
    ("tier", "gross", "fee", "reserve", "payout"),
    [
        (Tier.TRUSTED, 300_000, 30_000, 15_000, 255_000),
        (Tier.STANDARD, 300_000, 30_000, 30_000, 240_000),
        (Tier.NEW, 400_000, 40_000, 40_000, 320_000),
    ],
)
def test_settle_gross_matches_demo_table(
    tier: Tier, gross: int, fee: int, reserve: int, payout: int
) -> None:
    breakdown = settle_gross(gross, base_rate_bps(tier))
    assert (breakdown.fee_cents, breakdown.reserve_cents, breakdown.payout_cents) == (
        fee,
        reserve,
        payout,
    )


def test_settle_gross_rejects_negative() -> None:
    with pytest.raises(ValueError, match="gross"):
        settle_gross(-1, 1000)


def test_large_increase_needs_approval() -> None:
    decision = propose_reserve_change(
        seller_id="C",
        gross_cents=400_000,
        tier=Tier.NEW,
        current_rate_bps=1000,
        held_cents=40_000,
        signals=[CLUSTER, TRACKING],
    )
    assert decision.new_rate_bps == 3000
    assert decision.target_reserve_cents == 120_000
    assert decision.needs_approval is True
    assert decision.evidence_ids == ("ev-1",)


def test_small_increase_is_auto_approved() -> None:
    decision = propose_reserve_change(
        seller_id="B",
        gross_cents=300_000,
        tier=Tier.STANDARD,
        current_rate_bps=1000,
        held_cents=30_000,
        signals=[TRACKING],
    )
    assert decision.new_rate_bps == 1500
    assert decision.needs_approval is False


def test_cents_threshold_alone_triggers_approval() -> None:
    decision = propose_reserve_change(
        seller_id="X",
        gross_cents=10_000_000,
        tier=Tier.STANDARD,
        current_rate_bps=1000,
        held_cents=1_000_000,
        signals=[TRACKING],
    )
    assert decision.new_rate_bps - decision.old_rate_bps == 500
    assert decision.needs_approval is True


def test_shortfall_charges_up_to_balance_and_records_debt() -> None:
    plan = plan_adjustment(target_cents=120_000, held_cents=40_000, seller_balance_cents=50_000)
    assert (plan.charge_cents, plan.debt_cents, plan.release_cents) == (50_000, 30_000, 0)


def test_shortfall_fully_covered_leaves_no_debt() -> None:
    plan = plan_adjustment(target_cents=60_000, held_cents=40_000, seller_balance_cents=50_000)
    assert (plan.charge_cents, plan.debt_cents) == (20_000, 0)


def test_lower_target_cancels_debt_and_releases() -> None:
    plan = plan_adjustment(
        target_cents=60_000, held_cents=90_000, seller_balance_cents=0, existing_debt_cents=30_000
    )
    assert (plan.charge_cents, plan.debt_cents, plan.release_cents) == (0, 0, 30_000)
    assert plan.debt_cancelled_cents == 30_000


def test_plan_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError):
        plan_adjustment(target_cents=-1, held_cents=0, seller_balance_cents=0)


def test_clearing_only_applies_to_earlier_signals() -> None:
    assert compute_rate_bps(Tier.NEW, [DELIVERED, CLUSTER]) == 2500
