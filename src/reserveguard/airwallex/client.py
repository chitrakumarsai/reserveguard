"""The only interface the rest of ReserveGuard uses to move money."""

from typing import Literal, Protocol

# Airwallex transfer/charge statuses. NEW/PENDING are in flight; failures end in FAILED
# (API reference) or CANCELLED (hackathon guide).
TransferStatus = Literal["NEW", "PENDING", "SETTLED", "SUSPENDED", "FAILED", "CANCELLED"]
TRANSFER_STATUSES: frozenset[str] = frozenset(
    {"NEW", "PENDING", "SETTLED", "SUSPENDED", "FAILED", "CANCELLED"}
)
FINAL_STATUSES: frozenset[str] = frozenset({"SETTLED", "FAILED", "CANCELLED"})


class AirwallexError(Exception):
    """Base error for Airwallex client failures."""


class AirwallexTransientError(AirwallexError):
    """Retryable failure (timeout, 5xx, rate limit). Retry with the SAME idempotency key."""


class InsufficientFundsError(AirwallexError):
    """The source account cannot cover the requested amount."""


class AirwallexAPIError(AirwallexError):
    """A non-retryable error response from Airwallex."""

    def __init__(self, status_code: int, code: str, message: str = "") -> None:
        super().__init__(f"Airwallex {status_code} {code}: {message}".rstrip(": "))
        self.status_code = status_code
        self.code = code


class AirwallexClient(Protocol):
    """Amounts are integer minor units (cents). Money-moving calls take an idempotency key."""

    def get_balance(self, account_id: str | None, currency: str = "USD") -> int:
        """Available balance in minor units. ``None`` means the platform account."""
        ...

    def transfer_to_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """Platform -> seller connected account. Returns the transfer id."""
        ...

    def charge_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """Seller connected account -> platform. Returns the charge id."""
        ...

    def get_status(self, object_id: str) -> TransferStatus: ...
