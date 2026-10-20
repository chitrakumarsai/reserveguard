"""Run the ReserveGuard demo scenario.

Default: in-memory FakeAirwallexClient. With --live: the Airwallex sandbox, using the
sellers onboarded by scripts/sandbox_setup.py (moves sandbox money).
"""

import argparse
import sys
from datetime import UTC, datetime

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.agent.tools import format_pct, format_usd
from reserveguard.airwallex.client import AirwallexClient
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.config import Settings
from reserveguard.ledger import SellerTotals
from reserveguard.models import CheckResult, ReserveDecision
from reserveguard.reserve_engine import AdjustmentPlan
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    SAMPLE_EVIDENCE_PATH,
    build_service,
)
from reserveguard.settlement import SettlementLine, SettlementService

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


def all_settled(service: SettlementService, client: AirwallexClient) -> bool:
    """Live only: wait until Airwallex finishes every transfer and charge so far."""
    if not isinstance(client, RestAirwallexClient):
        return True
    ok = True
    for entry in service.ledger.entries:
        if entry.object_id is not None:
            status = client.wait_for_settlement(entry.object_id)
            if status != "SETTLED":
                print(f"  [FAIL] {entry.kind.value} {entry.object_id} ended {status}")
                ok = False
    return ok


def reconcile(service: SettlementService, client: AirwallexClient) -> bool:
    settled = all_settled(service, client)
    return print_checks(service.reconciliation()) and settled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the Airwallex sandbox")
    live = parser.parse_args().live

    offline = Settings(use_llm=False)
    service, client = build_service(Settings(use_fake_airwallex=not live))
    # Live idempotency keys derive from the cycle id, so each live run needs a fresh one.
    cycle_id = f"live-{datetime.now(UTC):%Y%m%d%H%M%S}" if live else "2026-10-cycle-1"
    seller_c = next(s for s in service.sellers if s.seller_id == SELLER_C)
    ok = True

    heading(f"Settlement cycle (USD, fee 10%){' - Airwallex sandbox' if live else ''}")
    print_settlement(service.settle_cycle(DEMO_GROSS_CENTS, cycle_id=cycle_id))
    ok &= reconcile(service, client)

    heading("Step 1: new risk evidence for Seller C")
    signals = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), offline)
    for s in signals:
        print(f"  signal {s.kind.value} (+{s.weight_bps} bps) from {', '.join(s.evidence_ids)}")
    print_decision(service.assess(SELLER_C, signals))
    ok &= reconcile(service, client)

    heading("Step 2: ops approval -> shortfall plan")
    if isinstance(client, FakeAirwallexClient):
        # Seller C spends most of its payout, so the charge can only partly cover the gap.
        excess = client.get_balance(seller_c.account_id) - C_WALLET_AFTER_WITHDRAWAL_CENTS
        client.withdraw(seller_c.account_id, excess)
    print(f"Seller C wallet before charge: {usd(client.get_balance(seller_c.account_id))}")
    result = service.approve(SELLER_C)
    print_plan(result.plan)
    print(f"Memo: {result.memo}")
    for seller_id in ("A", "B", SELLER_C):
        print_totals(service.ledger.totals(seller_id))
    ok &= reconcile(service, client)

    heading("Step 3: deliveries confirmed -> release")
    decision = service.assess(SELLER_C, classify_evidence(DELIVERY_CONFIRMATIONS, offline))
    print_decision(decision)
    print(f"Memo: {service.memos(SELLER_C)[-1]}")
    print_totals(service.ledger.totals(SELLER_C))
    ok &= reconcile(service, client)

    heading("Result")
    print("All reconciliation checks passed." if ok else "Some reconciliation checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
