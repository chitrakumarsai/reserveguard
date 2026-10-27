"""Reconcile the ledger against Airwallex's own records of each payment.

The ledger checks prove our books add up; these prove the provider agrees: every transfer
and charge we recorded exists on Airwallex for the same seller account, amount and currency,
and has SETTLED.
"""

import csv
import io
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from reserveguard.airwallex.money import from_major
from reserveguard.ledger import Ledger
from reserveguard.models import CheckResult, EntryKind, Seller


@dataclass(frozen=True)
class ProviderRecord:
    account_id: str
    amount_cents: int
    currency: str
    status: str


# (object_id, is_charge) -> what Airwallex says about that payment.
RecordFetcher = Callable[[str, bool], ProviderRecord]


def record_from_api(data: Mapping[str, Any], *, is_charge: bool) -> ProviderRecord:
    """Charges pull from the seller (``source``); transfers pay it (``destination``)."""
    currency = str(data.get("currency"))
    return ProviderRecord(
        account_id=str(data.get("source" if is_charge else "destination")),
        amount_cents=from_major(data.get("amount", 0), currency),
        currency=currency,
        status=str(data.get("status")),
    )


def check_against_provider(
    ledger: Ledger, sellers: Sequence[Seller], fetch: RecordFetcher
) -> list[CheckResult]:
    """One check per ledger entry that moved money on Airwallex."""
    by_id = {s.seller_id: s for s in sellers}
    checks = []
    for entry in ledger.entries:
        if entry.object_id is None:
            continue
        seller = by_id[entry.seller_id]
        record = fetch(entry.object_id, entry.kind is EntryKind.CHARGE)
        expected = ProviderRecord(seller.account_id, entry.amount_cents, seller.currency, "SETTLED")
        problems = [
            f"{field} {getattr(record, field)} != {getattr(expected, field)}"
            for field in ("amount_cents", "account_id", "currency", "status")
            if getattr(record, field) != getattr(expected, field)
        ]
        detail = "; ".join(problems) or (
            f"{record.amount_cents} {record.currency} {record.status} for {record.account_id}"
        )
        checks.append(
            CheckResult(
                name=f"provider:{entry.seller_id}:{entry.kind.value}:{entry.object_id}",
                passed=not problems,
                detail=detail.replace("amount_cents", "amount").replace("account_id", "account"),
            )
        )
    return checks


def report_mentions(csv_text: str, object_ids: set[str]) -> set[str]:
    """Which of our payment ids appear anywhere in an Airwallex platform report CSV."""
    cells = {cell for row in csv.reader(io.StringIO(csv_text)) for cell in row}
    return object_ids & cells
