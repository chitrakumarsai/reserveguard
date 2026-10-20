"""Read-only tools the LLM may call, plus memo drafting. No money movement here."""

from __future__ import annotations

from typing import TYPE_CHECKING

from reserveguard.models import ReserveDecision

if TYPE_CHECKING:
    from reserveguard.settlement import SettlementService


def format_pct(bps: int) -> str:
    return f"{bps // 100}.{bps % 100:02d}%"


def format_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def draft_memo(decision: ReserveDecision) -> str:
    """Deterministic memo for a reserve change. An LLM may later rewrite the prose only."""
    evidence = ", ".join(decision.evidence_ids) or "none"
    gate = (
        "Requires ops approval (above auto-approval thresholds)."
        if decision.needs_approval
        else "Auto-applied (below approval thresholds)."
    )
    return (
        f"Seller {decision.seller_id}: reserve rate {format_pct(decision.old_rate_bps)} -> "
        f"{format_pct(decision.new_rate_bps)}; target {format_usd(decision.old_target_cents)} -> "
        f"{format_usd(decision.target_reserve_cents)} (held {format_usd(decision.held_cents)}). "
        f"Evidence: {evidence}. {gate}"
    )


class ReadOnlyTools:
    """Views over one service. Every method is scoped to a single seller or is a listing."""

    def __init__(self, service: SettlementService) -> None:
        self._service = service

    def list_sellers(self) -> list[dict[str, str]]:
        return [
            {"seller_id": s.seller_id, "name": s.name, "tier": s.tier.value}
            for s in self._service.sellers
        ]

    def seller_summary(self, seller_id: str) -> dict[str, int | str]:
        totals = self._service.ledger.totals(seller_id)
        return {
            "seller_id": seller_id,
            "rate_bps": self._service.current_rate_bps(seller_id),
            "gross_cents": totals.gross_cents,
            "held_cents": totals.held_cents,
            "debt_cents": totals.debt_cents,
        }
