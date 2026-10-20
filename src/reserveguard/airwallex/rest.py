"""Airwallex REST implementation of AirwallexClient.

- Auth: ``POST /api/v1/authentication/login`` with ``x-client-id``/``x-api-key``; the
  30-minute bearer token is cached and refreshed shortly before ``expires_at``.
- Amounts: Airwallex uses major units; conversion is exact via ``money.py``.
- Idempotency: the caller's key is sent as ``request_id``. If a create's outcome is
  unknown (timeout / 5xx), the next attempt with that key first looks the object up
  by ``request_id`` and only re-sends the create if nothing exists.
- Tenancy: seller balances are read with ``x-on-behalf-of: <acct_id>``; transfers and
  charges name the seller in ``destination`` / ``source``.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import httpx

from reserveguard.airwallex.client import (
    FINAL_STATUSES,
    TRANSFER_STATUSES,
    AirwallexAPIError,
    AirwallexTransientError,
    InsufficientFundsError,
    TransferStatus,
)
from reserveguard.airwallex.money import from_major, to_major
from reserveguard.config import Settings

API = "/api/v1"
HTTP_TIMEOUT_S = 30.0
TOKEN_REFRESH_MARGIN_S = 60.0
MAX_RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF_S = 1.0
POLL_INTERVAL_S = 2.0
MAX_REFERENCE_LEN = 140
DEFAULT_TRANSFER_REASON = "goods_purchased"
TRANSFER_REASONS = frozenset(
    {
        "wages_salary",
        "donation_charitable_contribution",
        "personal_remittance",
        "transfer_to_own_account",
        "pension",
        "family_support",
        "living_expenses",
        "education_training",
        "travel",
        "investment_proceeds",
        "investment_capital",
        "loan_credit_repayment",
        "taxes",
        "goods_purchased",
        "business_expenses",
        "medical_services",
        "professional_business_services",
        "technical_services",
        "other_services",
        "construction",
        "freight",
        "real_estate",
    }
)


@dataclass(frozen=True)
class _Resource:
    create_path: str
    collection_path: str
    party_field: str


_TRANSFERS = _Resource(
    f"{API}/connected_account_transfers/create", f"{API}/connected_account_transfers", "destination"
)
_CHARGES = _Resource(f"{API}/charges/create", f"{API}/charges", "source")


def _parse_expiry(value: str) -> float:
    """Airwallex sends e.g. "2026-10-20T12:30:00+0000"; fromisoformat accepts it on 3.11+."""
    return datetime.fromisoformat(value).timestamp()


class RestAirwallexClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        transfer_reason: str = DEFAULT_TRANSFER_REASON,
    ) -> None:
        if not settings.airwallex_client_id or not settings.airwallex_api_key.get_secret_value():
            raise ValueError("AIRWALLEX_CLIENT_ID and AIRWALLEX_API_KEY must be set")
        if transfer_reason not in TRANSFER_REASONS:
            raise ValueError(f"unknown transfer reason {transfer_reason!r}")
        self.base_url = settings.airwallex_base_url.rstrip("/")
        self._client_id = settings.airwallex_client_id
        self._api_key = settings.airwallex_api_key
        self._http = http or httpx.Client(timeout=HTTP_TIMEOUT_S)
        self._clock = clock
        self._sleep = sleep
        self._reason = transfer_reason
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._resource_by_id: dict[str, _Resource] = {}
        self._uncertain_keys: set[str] = set()

    # --- AirwallexClient -------------------------------------------------------
    def get_balance(self, account_id: str | None, currency: str = "USD") -> int:
        """GET /api/v1/balances/current — available cash balance in minor units."""
        items = self.request("GET", f"{API}/balances/current", on_behalf_of=account_id)
        for item in items or []:
            if item.get("currency") == currency and item.get("account_type", "cash") == "cash":
                return from_major(item["available_amount"], currency)
        return 0

    def transfer_to_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """POST /api/v1/connected_account_transfers/create (platform -> seller)."""
        return self._create(
            _TRANSFERS, seller_account_id, amount_cents, currency, reference, idempotency_key
        )

    def charge_seller(
        self,
        seller_account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        idempotency_key: str,
    ) -> str:
        """POST /api/v1/charges/create (seller -> platform)."""
        return self._create(
            _CHARGES, seller_account_id, amount_cents, currency, reference, idempotency_key
        )

    def get_status(self, object_id: str) -> TransferStatus:
        known = self._resource_by_id.get(object_id)
        candidates = [known] if known else [_TRANSFERS, _CHARGES]
        for index, resource in enumerate(candidates):
            try:
                data = self.request("GET", f"{resource.collection_path}/{object_id}")
            except AirwallexAPIError as exc:
                if exc.status_code == 404 and index < len(candidates) - 1:
                    continue
                raise
            status = data.get("status")
            if status not in TRANSFER_STATUSES:
                raise AirwallexAPIError(200, "unexpected_status", f"{object_id} is {status}")
            return cast(TransferStatus, status)
        raise AirwallexAPIError(404, "not_found", object_id)  # pragma: no cover

    # --- extras ----------------------------------------------------------------
    def wait_for_settlement(
        self, object_id: str, *, timeout_s: float = 60.0, interval_s: float = POLL_INTERVAL_S
    ) -> TransferStatus:
        """Poll until SETTLED, FAILED or CANCELLED (transfers and charges start as NEW)."""
        deadline = self._clock() + timeout_s
        while True:
            status = self.get_status(object_id)
            if status in FINAL_STATUSES:
                return status
            if self._clock() >= deadline:
                raise AirwallexTransientError(f"{object_id} still {status} after {timeout_s}s")
            self._sleep(interval_s)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        on_behalf_of: str | None = None,
    ) -> Any:
        """Authenticated call with one re-login on 401 and backoff on 429."""
        reauthenticated = False
        rate_limited = 0
        while True:
            response = self._send(method, path, json, params, on_behalf_of)
            if response.status_code == 401 and not reauthenticated:
                reauthenticated = True
                self._token = None
                continue
            if response.status_code == 429:
                if rate_limited >= MAX_RATE_LIMIT_RETRIES:
                    raise AirwallexTransientError(f"rate limited on {path}")
                self._sleep(self._retry_after(response, rate_limited))
                rate_limited += 1
                continue
            return self._parse(response)

    # --- internals -------------------------------------------------------------
    def _create(
        self,
        resource: _Resource,
        account_id: str,
        amount_cents: int,
        currency: str,
        reference: str,
        key: str,
    ) -> str:
        if not 1 <= len(reference) <= MAX_REFERENCE_LEN:
            raise ValueError(f"reference must be 1-{MAX_REFERENCE_LEN} characters")
        if key in self._uncertain_keys:
            existing = self._find_by_request_id(resource, key)
            if existing is not None:
                return self._remember(existing, resource, key)
        body = {
            "request_id": key,
            "amount": to_major(amount_cents, currency),
            "currency": currency,
            resource.party_field: account_id,
            "reason": self._reason,
            "reference": reference,
        }
        try:
            data = self.request("POST", resource.create_path, json=body)
        except AirwallexTransientError:
            self._uncertain_keys.add(key)
            raise
        except AirwallexAPIError as exc:
            if exc.code != "request_id_duplicate":
                raise
            existing = self._find_by_request_id(resource, key)
            if existing is None:
                raise
            return self._remember(existing, resource, key)
        return self._remember(str(data["id"]), resource, key)

    def _find_by_request_id(self, resource: _Resource, key: str) -> str | None:
        data = self.request("GET", resource.collection_path, params={"request_id": key})
        for item in data.get("items", []):
            if item.get("request_id") == key:
                return str(item["id"])
        return None

    def _remember(self, object_id: str, resource: _Resource, key: str) -> str:
        self._uncertain_keys.discard(key)
        self._resource_by_id[object_id] = resource
        return object_id

    def _access_token(self) -> str:
        if self._token and self._clock() < self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
            return self._token
        headers = {
            "x-client-id": self._client_id,
            "x-api-key": self._api_key.get_secret_value(),
        }
        try:
            response = self._http.post(
                f"{self.base_url}{API}/authentication/login", headers=headers
            )
        except httpx.TransportError as exc:
            raise AirwallexTransientError("login failed: network error") from exc
        data = self._parse(response)
        self._token = str(data["token"])
        self._token_expires_at = _parse_expiry(str(data["expires_at"]))
        return self._token

    def _send(
        self,
        method: str,
        path: str,
        json: Mapping[str, Any] | None,
        params: Mapping[str, str] | None,
        on_behalf_of: str | None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if on_behalf_of:
            headers["x-on-behalf-of"] = on_behalf_of
        try:
            return self._http.request(
                method, f"{self.base_url}{path}", json=json, params=params, headers=headers
            )
        except httpx.TransportError as exc:
            raise AirwallexTransientError(f"{method} {path}: {type(exc).__name__}") from exc

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        if response.status_code < 400:
            return response.json() if response.content else {}
        if response.status_code >= 500:
            raise AirwallexTransientError(f"Airwallex {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        code = str(payload.get("code", "unknown"))
        message = str(payload.get("message", ""))
        if code == "insufficient_fund":
            raise InsufficientFundsError(message or code)
        raise AirwallexAPIError(response.status_code, code, message)

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after")
        try:
            return float(header) if header else RATE_LIMIT_BACKOFF_S * 2.0**attempt
        except ValueError:
            return RATE_LIMIT_BACKOFF_S * 2.0**attempt
