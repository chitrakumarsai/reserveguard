"""In-memory Airwallex stand-in for tests and the offline demo."""

import itertools
from collections.abc import Mapping

from reserveguard.airwallex.client import (
    AirwallexTransientError,
    InsufficientFundsError,
    TransferStatus,
)


class FakeAirwallexClient:
    def __init__(self, balances: Mapping[str | None, int]) -> None:
        self._balances: dict[str | None, int] = dict(balances)
        self._by_key: dict[str, str] = {}
        self._status: dict[str, TransferStatus] = {}
        self._ids = itertools.count(1)
        self._failures_left = 0
        self.calls: list[str] = []

    # --- test/demo helpers -------------------------------------------------
    def fail_next(self, count: int) -> None:
        """Make the next ``count`` money-moving calls raise a transient error."""
        self._failures_left = count

    def withdraw(self, account_id: str, amount_cents: int) -> None:
        """Simulate a seller moving funds out of their wallet."""
        self._debit(account_id, amount_cents)

    # --- AirwallexClient ---------------------------------------------------
    def get_balance(self, account_id: str | None, currency: str = "USD") -> int:
        """Single-currency fake: ``currency`` is accepted for protocol parity."""
        return self._balances.get(account_id, 0)

    def transfer_to_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        return self._move(None, seller_account_id, amount_cents, "tr", idempotency_key)

    def charge_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        return self._move(seller_account_id, None, amount_cents, "ch", idempotency_key)

    def get_status(self, object_id: str) -> TransferStatus:
        return self._status[object_id]

    # --- internals ---------------------------------------------------------
    def _move(
        self, source: str | None, dest: str | None, amount_cents: int, prefix: str, key: str
    ) -> str:
        self.calls.append(key)
        if self._failures_left > 0:
            self._failures_left -= 1
            raise AirwallexTransientError("simulated transient failure")
        if key in self._by_key:
            return self._by_key[key]
        if amount_cents <= 0:
            raise ValueError(f"amount_cents must be > 0, got {amount_cents}")
        self._debit(source, amount_cents)
        self._balances[dest] = self.get_balance(dest) + amount_cents
        object_id = f"{prefix}_{next(self._ids):06d}"
        self._by_key[key] = object_id
        self._status[object_id] = "SETTLED"
        return object_id

    def _debit(self, account_id: str | None, amount_cents: int) -> None:
        available = self.get_balance(account_id)
        if amount_cents > available:
            raise InsufficientFundsError(
                f"account {account_id or 'platform'} has {available}, needs {amount_cents}"
            )
        self._balances[account_id] = available - amount_cents
