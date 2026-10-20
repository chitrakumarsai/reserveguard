"""Real Airwallex REST client — skeleton only.

Seller-scoped calls must send ``x-on-behalf-of: <connected account id>`` so the
request acts on that seller's account and never another tenant's.
"""

from reserveguard.airwallex.client import TransferStatus
from reserveguard.config import Settings


class RestAirwallexClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.airwallex_base_url
        self._client_id = settings.airwallex_client_id
        self._api_key = settings.airwallex_api_key
        # TODO: obtain a bearer token via POST /api/v1/authentication/login and cache it.

    def get_balance(self, account_id: str | None) -> int:
        """GET /api/v1/balances/current (with x-on-behalf-of for a seller)."""
        raise NotImplementedError("TODO: implement balances lookup")

    def transfer_to_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """POST /api/v1/connected_account_transfers/create with request_id=idempotency_key."""
        raise NotImplementedError("TODO: implement connected account transfer")

    def charge_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """POST /api/v1/charges/create with x-on-behalf-of: <seller_account_id>."""
        raise NotImplementedError("TODO: implement charge")

    def get_status(self, object_id: str) -> TransferStatus:
        """GET the transfer/charge by id; map Airwallex status to NEW or SETTLED."""
        raise NotImplementedError("TODO: implement status lookup")
