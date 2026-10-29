"""Append-only, per-seller ledger and reconciliation checks."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from reserveguard.models import CheckResult, EntryKind, LedgerEntry


@dataclass(frozen=True)
class SellerTotals:
    seller_id: str
    gross_cents: int = 0
    fee_cents: int = 0
    reserve_hold_cents: int = 0
    payouts_cents: int = 0
    charges_cents: int = 0
    refunds_cents: int = 0
    released_cents: int = 0
    debt_cents: int = 0

    @property
    def held_cents(self) -> int:
        return self.reserve_hold_cents + self.charges_cents - self.released_cents

    @property
    def net_paid_cents(self) -> int:
        return self.payouts_cents - self.charges_cents

    @property
    def owed_cents(self) -> int:
        return self.gross_cents - self.fee_cents


@dataclass(frozen=True)
class Ledger:
    entries: tuple[LedgerEntry, ...] = field(default_factory=tuple)

    def record(self, entry: LedgerEntry) -> "Ledger":
        return Ledger(entries=(*self.entries, entry))

    def seller_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.seller_id for e in self.entries))

    def totals(self, seller_id: str) -> SellerTotals:
        """Totals for ONE seller. Only that seller's entries are read."""
        sums = dict.fromkeys(EntryKind, 0)
        for entry in self.entries:
            if entry.seller_id == seller_id:
                sums[entry.kind] += entry.amount_cents
        return SellerTotals(
            seller_id=seller_id,
            gross_cents=sums[EntryKind.GROSS],
            fee_cents=sums[EntryKind.FEE],
            reserve_hold_cents=sums[EntryKind.RESERVE_HOLD],
            payouts_cents=sums[EntryKind.PAYOUT],
            charges_cents=sums[EntryKind.CHARGE],
            refunds_cents=sums[EntryKind.REFUND],
            released_cents=sums[EntryKind.RELEASE],
            debt_cents=sums[EntryKind.DEBT] - sums[EntryKind.DEBT_CANCEL],
        )


def _seller_identity(t: SellerTotals) -> CheckResult:
    lhs = t.net_paid_cents + t.refunds_cents + t.released_cents + t.held_cents
    return CheckResult(
        name=f"seller_identity:{t.seller_id}",
        passed=lhs == t.owed_cents,
        detail=f"paid-charges+refunds+released+held={lhs}, gross-fee={t.owed_cents}",
    )


def _tenant_isolation(t: SellerTotals) -> CheckResult:
    """A seller can never have received more than their own net, nor hold a negative reserve."""
    received = t.net_paid_cents + t.released_cents
    ok = received + t.refunds_cents <= t.owed_cents and t.held_cents >= 0 and t.debt_cents >= 0
    return CheckResult(
        name=f"tenant_isolation:{t.seller_id}",
        passed=ok,
        detail=f"received={received}, owed={t.owed_cents}, held={t.held_cents}",
    )


def _platform_balance(totals: Iterable[SellerTotals], platform_balance_cents: int) -> CheckResult:
    items = list(totals)
    fees = sum(t.fee_cents for t in items)
    reserves = sum(t.held_cents for t in items)
    return CheckResult(
        name="platform_balance",
        passed=platform_balance_cents == fees + reserves,
        detail=f"platform={platform_balance_cents}, fees={fees}, unreleased_reserves={reserves}",
    )


def run_checks(ledger: Ledger, platform_balance_cents: int) -> list[CheckResult]:
    totals = [ledger.totals(seller_id) for seller_id in ledger.seller_ids()]
    per_seller = [check(t) for t in totals for check in (_seller_identity, _tenant_isolation)]
    return [*per_seller, _platform_balance(totals, platform_balance_cents)]
