"""Cross-check the ledger against Airwallex's own records of each payment."""

import pytest

from reserveguard.ledger import Ledger
from reserveguard.models import EntryKind, LedgerEntry, Seller, Tier
from reserveguard.provider_check import (
    ProviderRecord,
    check_against_provider,
    record_from_api,
    report_mentions,
)

pytestmark = pytest.mark.unit

SELLERS = (Seller("C", "Seller C", Tier.NEW, "acct_c"),)


def _ledger(*entries: tuple[EntryKind, int, str | None]) -> Ledger:
    ledger = Ledger()
    for kind, cents, object_id in entries:
        ledger = ledger.record(
            LedgerEntry(
                seller_id="C", kind=kind, amount_cents=cents, reference="r", object_id=object_id
            )
        )
    return ledger


def _fetch(records: dict[str, ProviderRecord]):  # type: ignore[no-untyped-def]
    return lambda object_id, _is_charge: records[object_id]


def test_matching_records_pass_and_unlinked_entries_are_skipped() -> None:
    ledger = _ledger(
        (EntryKind.GROSS, 400_000, None),
        (EntryKind.PAYOUT, 300_000, "tr_1"),
        (EntryKind.CHARGE, 80_000, "ch_1"),
    )
    records = {
        "tr_1": ProviderRecord("acct_c", 300_000, "USD", "SETTLED"),
        "ch_1": ProviderRecord("acct_c", 80_000, "USD", "SETTLED"),
    }

    checks = check_against_provider(ledger, SELLERS, _fetch(records))

    assert [c.name for c in checks] == ["provider:C:payout:tr_1", "provider:C:charge:ch_1"]
    assert all(c.passed for c in checks)


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        (ProviderRecord("acct_c", 299_999, "USD", "SETTLED"), "amount"),
        (ProviderRecord("acct_other", 300_000, "USD", "SETTLED"), "account"),
        (ProviderRecord("acct_c", 300_000, "EUR", "SETTLED"), "currency"),
        (ProviderRecord("acct_c", 300_000, "USD", "PENDING"), "status"),
    ],
)
def test_any_mismatch_fails_and_says_what_differs(record: ProviderRecord, problem: str) -> None:
    ledger = _ledger((EntryKind.PAYOUT, 300_000, "tr_1"))

    [check] = check_against_provider(ledger, SELLERS, _fetch({"tr_1": record}))

    assert not check.passed
    assert problem in check.detail


def test_charges_are_looked_up_as_charges() -> None:
    seen: list[tuple[str, bool]] = []

    def fetch(object_id: str, is_charge: bool) -> ProviderRecord:
        seen.append((object_id, is_charge))
        return ProviderRecord("acct_c", 1, "USD", "SETTLED")

    ledger = _ledger((EntryKind.RELEASE, 1, "tr_9"), (EntryKind.CHARGE, 1, "ch_9"))
    check_against_provider(ledger, SELLERS, fetch)

    assert seen == [("tr_9", False), ("ch_9", True)]


def test_record_from_api_reads_the_right_account_field_and_converts_to_cents() -> None:
    transfer = {"destination": "acct_c", "amount": 600.0, "currency": "USD", "status": "SETTLED"}
    charge = {"source": "acct_c", "amount": 800.5, "currency": "USD", "status": "SETTLED"}

    assert record_from_api(transfer, is_charge=False) == ProviderRecord(
        "acct_c", 60_000, "USD", "SETTLED"
    )
    assert record_from_api(charge, is_charge=True).amount_cents == 80_050


def test_report_mentions_finds_ids_anywhere_in_the_csv() -> None:
    csv_text = "id,amount\ntr_1,300\nother,1\n"

    assert report_mentions(csv_text, {"tr_1", "ch_1"}) == {"tr_1"}
