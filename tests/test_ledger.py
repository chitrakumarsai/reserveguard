"""Unit tests for the per-seller ledger and reconciliation checks."""

import pytest

from reserveguard.ledger import Ledger, run_checks
from reserveguard.models import EntryKind, LedgerEntry

pytestmark = pytest.mark.unit


def _entry(seller: str, kind: EntryKind, cents: int) -> LedgerEntry:
    return LedgerEntry(seller_id=seller, kind=kind, amount_cents=cents, reference="t")


def _settled(seller: str, gross: int, fee: int, reserve: int) -> Ledger:
    ledger = Ledger()
    for kind, cents in [
        (EntryKind.GROSS, gross),
        (EntryKind.FEE, fee),
        (EntryKind.RESERVE_HOLD, reserve),
        (EntryKind.PAYOUT, gross - fee - reserve),
    ]:
        ledger = ledger.record(_entry(seller, kind, cents))
    return ledger


def test_record_returns_new_ledger() -> None:
    empty = Ledger()
    one = empty.record(_entry("A", EntryKind.GROSS, 100))
    assert empty.entries == ()
    assert len(one.entries) == 1


def test_record_rejects_negative_amount() -> None:
    with pytest.raises(ValueError):
        _entry("A", EntryKind.GROSS, -5)


def test_totals_are_scoped_to_one_seller() -> None:
    ledger = _settled("A", 300_000, 30_000, 15_000)
    ledger = ledger.merge(_settled("B", 300_000, 30_000, 30_000))
    totals = ledger.totals("A")
    assert totals.gross_cents == 300_000
    assert totals.held_cents == 15_000
    assert totals.net_paid_cents == 255_000


def test_charges_raise_held_and_debt_is_separate() -> None:
    ledger = _settled("C", 400_000, 40_000, 40_000)
    ledger = ledger.record(_entry("C", EntryKind.CHARGE, 50_000))
    ledger = ledger.record(_entry("C", EntryKind.DEBT, 30_000))
    totals = ledger.totals("C")
    assert totals.held_cents == 90_000
    assert totals.debt_cents == 30_000
    assert totals.net_paid_cents == 270_000


def test_release_and_debt_cancel() -> None:
    ledger = _settled("C", 400_000, 40_000, 40_000)
    for kind, cents in [
        (EntryKind.CHARGE, 50_000),
        (EntryKind.DEBT, 30_000),
        (EntryKind.DEBT_CANCEL, 30_000),
        (EntryKind.RELEASE, 30_000),
    ]:
        ledger = ledger.record(_entry("C", kind, cents))
    totals = ledger.totals("C")
    assert (totals.held_cents, totals.debt_cents, totals.released_cents) == (60_000, 0, 30_000)


def test_all_checks_pass_for_consistent_state() -> None:
    ledger = _settled("A", 300_000, 30_000, 15_000).merge(_settled("B", 300_000, 30_000, 30_000))
    results = run_checks(ledger, platform_balance_cents=105_000)
    assert results
    assert all(r.passed for r in results), results


def test_platform_check_fails_on_mismatch() -> None:
    ledger = _settled("A", 300_000, 30_000, 15_000)
    results = {r.name: r for r in run_checks(ledger, platform_balance_cents=1)}
    assert results["platform_balance"].passed is False


def test_seller_identity_fails_when_payout_missing() -> None:
    ledger = Ledger()
    for kind, cents in [(EntryKind.GROSS, 100_000), (EntryKind.FEE, 10_000)]:
        ledger = ledger.record(_entry("A", kind, cents))
    results = {r.name: r for r in run_checks(ledger, platform_balance_cents=10_000)}
    assert results["seller_identity:A"].passed is False


def test_isolation_fails_when_seller_overpaid() -> None:
    ledger = _settled("A", 100_000, 10_000, 10_000).record(_entry("A", EntryKind.PAYOUT, 50_000))
    results = {r.name: r for r in run_checks(ledger, platform_balance_cents=0)}
    assert results["tenant_isolation:A"].passed is False


def test_refunds_count_toward_identity() -> None:
    ledger = Ledger()
    for kind, cents in [
        (EntryKind.GROSS, 100_000),
        (EntryKind.FEE, 10_000),
        (EntryKind.RESERVE_HOLD, 10_000),
        (EntryKind.REFUND, 5_000),
        (EntryKind.PAYOUT, 75_000),
    ]:
        ledger = ledger.record(_entry("A", kind, cents))
    results = {r.name: r for r in run_checks(ledger, platform_balance_cents=20_000)}
    assert results["seller_identity:A"].passed is True
