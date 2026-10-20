"""The only interface the rest of ReserveGuard uses to move money."""

from typing import Literal, Protocol

TransferStatus = Literal["NEW", "SETTLED"]


class AirwallexError(Exception):
    """Base error for Airwallex client failures."""


class AirwallexTransientError(AirwallexError):
    """Retryable failure (timeout, 5xx). Retry with the SAME idempotency key."""


class InsufficientFundsError(AirwallexError):
    """The source account cannot cover the requested amount."""


class AirwallexClient(Protocol):
    """All amounts are integer cents. Money-moving calls require an idempotency key."""

    def get_balance(self, account_id: str | None) -> int:
        """Available balance in cents. ``None`` means the platform account."""
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
