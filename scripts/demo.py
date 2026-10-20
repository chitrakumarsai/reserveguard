"""Run the ReserveGuard demo scenario against FakeAirwallexClient."""

import sys

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.agent.tools import format_pct, format_usd
from reserveguard.config import Settings
from reserveguard.ledger import SellerTotals
from reserveguard.models import CheckResult, ReserveDecision
from reserveguard.reserve_engine import AdjustmentPlan
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    SAMPLE_EVIDENCE_PATH,
    build_demo_service,
)
from reserveguard.settlement import SettlementLine

SELLER_C = "C"
C_WALLET_AFTER_WITHDRAWAL_CENTS = 50_000


usd = format_usd


def heading(title: str) -> None:
    print(f"\n=== {title} ===")


def print_settlement(lines: list[SettlementLine]) -> None:
    print(f"{'Seller':<7}{'Gross':>12}{'Fee':>10}{'Rate':>7}{'Reserve':>10}{'Payout':>12}")
    for line in lines:
        print(
            f"{line.seller_id:<7}{usd(line.gross_cents):>12}{usd(line.fee_cents):>10}"
            f"{format_pct(line.rate_bps):>7}{usd(line.reserve_cents):>10}{usd(line.payout_cents):>12}"
        )
    print(
        f"{'Total':<7}{usd(sum(x.gross_cents for x in lines)):>12}"
        f"{usd(sum(x.fee_cents for x in lines)):>10}{'':>7}"
        f"{usd(sum(x.reserve_cents for x in lines)):>10}"
        f"{usd(sum(x.payout_cents for x in lines)):>12}"
    )


def print_decision(decision: ReserveDecision) -> None:
    print(
        f"Seller {decision.seller_id}: rate {format_pct(decision.old_rate_bps)} -> "
        f"{format_pct(decision.new_rate_bps)}, target reserve {usd(decision.target_reserve_cents)}"
    )
    print(f"Cited evidence: {', '.join(decision.evidence_ids)}")
    print(f"Needs approval: {decision.needs_approval}")


def print_plan(plan: AdjustmentPlan) -> None:
    print(
        f"charge {usd(plan.charge_cents)}, debt {usd(plan.debt_cents)}, "
        f"release {usd(plan.release_cents)}, debt cancelled {usd(plan.debt_cancelled_cents)}"
    )


def print_totals(t: SellerTotals) -> None:
    print(
        f"Seller {t.seller_id}: net paid {usd(t.net_paid_cents)} + released "
        f"{usd(t.released_cents)} + held {usd(t.held_cents)} = "
        f"{usd(t.net_paid_cents + t.released_cents + t.held_cents)} (owed {usd(t.owed_cents)}); "
        f"debt {usd(t.debt_cents)}"
    )


def print_checks(results: list[CheckResult]) -> bool:
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.name}: {r.detail}")
    return all(r.passed for r in results)


def main() -> int:
    offline = Settings(use_llm=False)
    service, fake = build_demo_service()
    ok = True

    heading("Settlement cycle (USD, fee 10%)")
    print_settlement(service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="2026-10-cycle-1"))
    ok &= print_checks(service.reconciliation())

    heading("Step 1: new risk evidence for Seller C")
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), offline)
    for s in signals:
        print(f"  signal {s.kind.value} (+{s.weight_bps} bps) from {', '.join(s.evidence_ids)}")
    print_decision(service.assess(SELLER_C, signals))
    ok &= print_checks(service.reconciliation())

    heading("Step 2: ops approval -> shortfall plan")
    excess = fake.get_balance("acct_seller_c") - C_WALLET_AFTER_WITHDRAWAL_CENTS
    fake.withdraw("acct_seller_c", excess)
    print(f"Seller C wallet before charge: {usd(fake.get_balance('acct_seller_c'))}")
    result = service.approve(SELLER_C)
    print_plan(result.plan)
    print(f"Memo: {result.memo}")
    for seller_id in ("A", "B", SELLER_C):
        print_totals(service.ledger.totals(seller_id))
    ok &= print_checks(service.reconciliation())

    heading("Step 3: deliveries confirmed -> release")
    decision = service.assess(SELLER_C, classify_evidence(DELIVERY_CONFIRMATIONS, offline))
    print_decision(decision)
    print(f"Memo: {service.memos(SELLER_C)[-1]}")
    print_totals(service.ledger.totals(SELLER_C))
    ok &= print_checks(service.reconciliation())

    heading("Result")
    print("All reconciliation checks passed." if ok else "Some reconciliation checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
