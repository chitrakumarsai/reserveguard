"""Pure, deterministic reserve maths. No I/O, no LLM, no API calls."""

from collections.abc import Sequence
from dataclasses import dataclass

from reserveguard.models import ReserveDecision, RiskKind, RiskSignal, Tier

BPS_DENOMINATOR = 10_000
PLATFORM_FEE_BPS = 1_000
MAX_RATE_BPS = 4_000
APPROVAL_RATE_INCREASE_BPS = 1_500
APPROVAL_AMOUNT_INCREASE_CENTS = 50_000

_BASE_RATES_BPS: dict[Tier, int] = {
    Tier.TRUSTED: 500,
    Tier.STANDARD: 1_000,
    Tier.NEW: 1_500,  # Kit 8: the newest seller carries the highest reserve
}


@dataclass(frozen=True)
class SettlementBreakdown:
    gross_cents: int
    fee_cents: int
    rate_bps: int
    reserve_cents: int
    payout_cents: int
    debt_collected_cents: int = 0


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


@dataclass(frozen=True)
class RateLine:
    """One signal's part in the rate. ``status``: "active" (counted), "cleared" (its points
    were removed by a later signal) or "clears" (it removes an earlier signal's points)."""

    kind: RiskKind
    weight_bps: int
    evidence_ids: tuple[str, ...]
    summary: str
    status: str
    cleared_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class RateExplanation:
    base_bps: int
    lines: tuple[RateLine, ...]
    uncapped_bps: int
    rate_bps: int

    @property
    def capped(self) -> bool:
        return self.rate_bps < self.uncapped_bps


def explain_rate(tier: Tier, signals: Sequence[RiskSignal]) -> RateExplanation:
    """Base tier rate plus active signal weights, capped at MAX_RATE_BPS, line by line.

    A signal with ``clears`` set removes the points of the first not-yet-cleared
    EARLIER signal of that kind; it adds its own weight as well (usually 0).
    """
    cleared_by: dict[int, tuple[str, ...]] = {}
    for position, signal in enumerate(signals):
        if signal.clears is None:
            continue
        for index, target in enumerate(signals[:position]):
            if index not in cleared_by and target.kind == signal.clears and target.clears is None:
                cleared_by[index] = signal.evidence_ids
                break
    lines = tuple(
        RateLine(
            kind=s.kind,
            weight_bps=s.weight_bps,
            evidence_ids=s.evidence_ids,
            summary=s.summary,
            status="cleared" if i in cleared_by else "clears" if s.clears else "active",
            cleared_by=cleared_by.get(i, ()),
        )
        for i, s in enumerate(signals)
    )
    uncapped = base_rate_bps(tier) + sum(
        line.weight_bps for line in lines if line.status != "cleared"
    )
    return RateExplanation(
        base_bps=base_rate_bps(tier),
        lines=lines,
        uncapped_bps=uncapped,
        rate_bps=min(uncapped, MAX_RATE_BPS),
    )


def compute_rate_bps(tier: Tier, signals: Sequence[RiskSignal]) -> int:
    return explain_rate(tier, signals).rate_bps


def _apply_bps(cents: int, rate_bps: int) -> int:
    return cents * rate_bps // BPS_DENOMINATOR


def reserve_for(gross_cents: int, rate_bps: int) -> int:
    return _apply_bps(gross_cents, rate_bps)


def settle_gross(gross_cents: int, rate_bps: int, debt_cents: int = 0) -> SettlementBreakdown:
    """The seller's reserve debt is repaid from this payout first, up to the whole payout."""
    if gross_cents < 0:
        raise ValueError(f"gross_cents must be >= 0, got {gross_cents}")
    if debt_cents < 0:
        raise ValueError(f"debt_cents must be >= 0, got {debt_cents}")
    fee = _apply_bps(gross_cents, PLATFORM_FEE_BPS)
    reserve = reserve_for(gross_cents, rate_bps)
    collected = min(debt_cents, gross_cents - fee - reserve)
    return SettlementBreakdown(
        gross_cents=gross_cents,
        fee_cents=fee,
        rate_bps=rate_bps,
        reserve_cents=reserve,
        payout_cents=gross_cents - fee - reserve - collected,
        debt_collected_cents=collected,
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
