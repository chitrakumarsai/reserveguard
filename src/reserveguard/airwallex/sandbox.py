"""Sandbox-only setup: fund the platform, onboard connected sellers, pull platform reports.

These are not part of AirwallexClient: they are operator/setup actions, never called by
settlement logic or the LLM.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reserveguard.airwallex.client import AirwallexAPIError
from reserveguard.airwallex.money import to_major
from reserveguard.airwallex.rest import API, RestAirwallexClient

REPORT_TYPES = frozenset(
    {
        "TRANSACTION_RECON_REPORT",
        "PAYOUT_REPORT",
        "BALANCE_REPORT",
        "ACCOUNT_REPORT",
        "ISSUING_TRANSACTION_REPORT",
        "APPLICATION_FEE_REPORT",
        "PLATFORM_LIQUIDITY_TRANSACTION_REPORT",
        "PLATFORM_LIQUIDITY_EOD_BALANCE_REPORT",
    }
)


@dataclass(frozen=True)
class ConnectedAccountSpec:
    """Minimal US business seller. Use business name "Sandbox Business" for auto-KYB."""

    nickname: str
    business_name: str
    ein: str
    email: str
    address_line1: str
    city: str
    state: str
    postcode: str
    country_code: str = "US"


@dataclass(frozen=True)
class PlatformReport:
    report_id: str
    status: str
    download_url: str | None


def build_account_body(spec: ConnectedAccountSpec, agreed_at: datetime) -> dict[str, Any]:
    """Body for POST /accounts/create. The EIN must be present before submit, or
    on-behalf-of transfers fail, and accounts cannot be edited after submission."""
    return {
        "nickname": spec.nickname,
        "primary_contact": {"email": spec.email},
        "account_details": {
            "legal_entity_type": "BUSINESS",
            "business_details": {
                "business_name": spec.business_name,
                "business_identifiers": [
                    {"country_code": spec.country_code, "number": spec.ein, "type": "EIN"}
                ],
                "registration_address": {
                    "address_line1": spec.address_line1,
                    "suburb": spec.city,
                    "state": spec.state,
                    "postcode": spec.postcode,
                    "country_code": spec.country_code,
                },
            },
        },
        "customer_agreements": {
            "agreed_to_terms_and_conditions": True,
            "terms_and_conditions": {
                "agreed_at": agreed_at.isoformat(),
                "service_agreement_type": "FULL",
            },
        },
    }


class SandboxAdmin:
    def __init__(self, client: RestAirwallexClient) -> None:
        if "sandbox" not in client.base_url:
            raise ValueError("SandboxAdmin only runs against the Airwallex sandbox")
        self._client = client

    def ensure_global_account(self, currency: str, country_code: str) -> str:
        """Reuse an ACTIVE global account in ``currency``; create one only if none exists."""
        data = self._client.request("GET", f"{API}/global_accounts")
        for item in data.get("items", []):
            features = [*item.get("required_features", []), *item.get("supported_features", [])]
            currencies = {f.get("currency") for f in features}
            if item.get("status") == "ACTIVE" and currency in currencies:
                return str(item["id"])
        created = self._client.request(
            "POST",
            f"{API}/global_accounts/create",
            json={
                "country_code": country_code,
                "request_id": str(uuid.uuid4()),
                "required_features": [{"currency": currency, "transfer_method": "LOCAL"}],
            },
        )
        return str(created["id"])

    def simulate_deposit(self, global_account_id: str, amount_cents: int, currency: str) -> str:
        """POST /simulation/deposit/create: funds the platform wallet (PENDING, posts at once)."""
        body = {
            "global_account_id": global_account_id,
            # This endpoint documents a JSON number; the string is exact so float() is too.
            "amount": float(to_major(amount_cents, currency)),
            "reference": "ReserveGuard sandbox funding",
        }
        return str(
            self._client.request("POST", f"{API}/simulation/deposit/create", json=body)["id"]
        )

    def onboard_seller(self, spec: ConnectedAccountSpec) -> str:
        """create -> submit -> simulate ACTIVE -> confirm. Returns the connected account id."""
        body = build_account_body(spec, datetime.now(UTC))
        account_id = str(self._client.request("POST", f"{API}/accounts/create", json=body)["id"])
        self._client.request("POST", f"{API}/accounts/{account_id}/submit")
        self._client.request(
            "POST",
            f"{API}/simulation/accounts/{account_id}/update_status",
            json={"next_status": "ACTIVE", "force": True},
        )
        status = self._client.request("GET", f"{API}/accounts/{account_id}").get("status")
        if status != "ACTIVE":
            raise AirwallexAPIError(200, "account_not_active", f"{account_id} is {status}")
        return account_id

    def create_platform_report(
        self, report_type: str, from_created_at: str, to_created_at: str
    ) -> str:
        """POST /platform_reports/create (CSV). ``to_created_at`` is exclusive; max 366 days."""
        if report_type not in REPORT_TYPES:
            raise ValueError(f"unknown report type {report_type!r}")
        body = {
            "type": report_type,
            "file_format": "CSV",
            "from_created_at": from_created_at,
            "to_created_at": to_created_at,
        }
        return str(self._client.request("POST", f"{API}/platform_reports/create", json=body)["id"])

    def get_platform_report(self, report_id: str) -> PlatformReport:
        """download_url appears only when COMPLETED and expires one minute later."""
        data = self._client.request("GET", f"{API}/platform_reports/{report_id}")
        return PlatformReport(
            report_id=report_id,
            status=str(data.get("status")),
            download_url=data.get("download_url"),
        )
