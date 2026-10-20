"""Pure, deterministic reserve maths. No I/O, no LLM, no API calls."""

from collections.abc import Sequence
from dataclasses import dataclass

from reserveguard.models import ReserveDecision, RiskSignal, Tier

BPS_DENOMINATOR = 10_000
PLATFORM_FEE_BPS = 1_000
MAX_RATE_BPS = 4_000
APPROVAL_RATE_INCREASE_BPS = 1_500
APPROVAL_AMOUNT_INCREASE_CENTS = 50_000

_BASE_RATES_BPS: dict[Tier, int] = {
    Tier.TRUSTED: 500,
    Tier.STANDARD: 1_000,
    Tier.NEW: 1_000,
}


@dataclass(frozen=True)
class SettlementBreakdown:
    gross_cents: int
    fee_cents: int
    rate_bps: int
    reserve_cents: int
    payout_cents: int


@dataclass(frozen=True)
class AdjustmentPlan:
    """What to do to move a seller's held reserve toward a new target."""

    target_cents: int
    held_cents: int
    charge_cents: int = 0
    debt_cents: int = 0
    release_cents: int = 0
    debt_cancelled_cents: int = 0


def base_rate_bps(tier: Tier) -> int:
    return _BASE_RATES_BPS[tier]


def compute_rate_bps(tier: Tier, signals: Sequence[RiskSignal]) -> int:
    """Base tier rate plus active signal weights, capped at MAX_RATE_BPS.

    A signal with ``clears`` set removes the points of the first not-yet-cleared
    EARLIER signal of that kind; it adds its own weight as well (usually 0).
    """
    cleared: set[int] = set()
    for position, signal in enumerate(signals):
        if signal.clears is None:
            continue
        for index, target in enumerate(signals[:position]):
            if index not in cleared and target.kind == signal.clears and target.clears is None:
                cleared.add(index)
                break
    added = sum(s.weight_bps for i, s in enumerate(signals) if i not in cleared)
    return min(base_rate_bps(tier) + added, MAX_RATE_BPS)


def _apply_bps(cents: int, rate_bps: int) -> int:
    return cents * rate_bps // BPS_DENOMINATOR


def reserve_for(gross_cents: int, rate_bps: int) -> int:
    return _apply_bps(gross_cents, rate_bps)


def settle_gross(gross_cents: int, rate_bps: int) -> SettlementBreakdown:
    if gross_cents < 0:
        raise ValueError(f"gross_cents must be >= 0, got {gross_cents}")
    fee = _apply_bps(gross_cents, PLATFORM_FEE_BPS)
    reserve = reserve_for(gross_cents, rate_bps)
    return SettlementBreakdown(
        gross_cents=gross_cents,
        fee_cents=fee,
        rate_bps=rate_bps,
        reserve_cents=reserve,
        payout_cents=gross_cents - fee - reserve,
    )


def propose_reserve_change(
    *,
    seller_id: str,
    gross_cents: int,
    tier: Tier,
    current_rate_bps: int,
    held_cents: int,
    signals: Sequence[RiskSignal],
) -> ReserveDecision:
    new_rate = compute_rate_bps(tier, signals)
    old_target = reserve_for(gross_cents, current_rate_bps)
    new_target = reserve_for(gross_cents, new_rate)
    needs_approval = (
        new_rate - current_rate_bps > APPROVAL_RATE_INCREASE_BPS
        or new_target - old_target > APPROVAL_AMOUNT_INCREASE_CENTS
    )
    evidence_ids = tuple(dict.fromkeys(eid for s in signals for eid in s.evidence_ids))
    return ReserveDecision(
        seller_id=seller_id,
        old_rate_bps=current_rate_bps,
        new_rate_bps=new_rate,
        old_target_cents=old_target,
        target_reserve_cents=new_target,
        held_cents=held_cents,
        needs_approval=needs_approval,
        evidence_ids=evidence_ids,
    )


def plan_adjustment(
    *,
    target_cents: int,
    held_cents: int,
    seller_balance_cents: int,
    existing_debt_cents: int = 0,
) -> AdjustmentPlan:
    """Shortfall: charge min(shortfall, balance), rest is debt. Surplus: cancel debt, release."""
    if min(target_cents, held_cents, seller_balance_cents, existing_debt_cents) < 0:
        raise ValueError("adjustment inputs must be non-negative")
    if target_cents > held_cents:
        shortfall = target_cents - held_cents
        charge = min(shortfall, seller_balance_cents)
        return AdjustmentPlan(
            target_cents=target_cents,
            held_cents=held_cents,
            charge_cents=charge,
            debt_cents=shortfall - charge,
        )
    return AdjustmentPlan(
        target_cents=target_cents,
        held_cents=held_cents,
        release_cents=held_cents - target_cents,
        debt_cancelled_cents=existing_debt_cents,
    )
