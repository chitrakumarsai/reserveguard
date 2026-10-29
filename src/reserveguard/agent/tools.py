"""Memo drafting and money formatting. No money movement here."""

from reserveguard.models import ReserveDecision


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
