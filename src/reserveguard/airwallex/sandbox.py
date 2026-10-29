"""Sandbox-only setup: onboard connected sellers, pull platform reports.

These are not part of AirwallexClient: they are operator/setup actions, never called by
settlement logic or the LLM.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reserveguard.airwallex.client import AirwallexAPIError
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
# Snapshot reports: Airwallex rejects any created_at/updated_at filter on these.
UNDATED_REPORT_TYPES = frozenset({"BALANCE_REPORT", "ACCOUNT_REPORT"})

# KYB answers the sandbox requires before /submit. Test values only.
SELLER_BUSINESS_STRUCTURE = "CORPORATION"
SELLER_GOODS_DESCRIPTION = "Online marketplace seller of consumer goods"
SELLER_INDUSTRY_CODE = "ICCV3_1025XX"  # Retail > Toys and hobbies
SELLER_PRODUCTS = ("COLLECT_MARKETPLACE_PROCEEDS", "RECEIVE_TRANSFERS")
SELLER_MONTHLY_REVENUE_USD = "50000"
SELLER_OWNER_DOB = "1985-04-12"
SELLER_OWNER_PASSPORT = "123456789"
SELLER_OWNER_ROLES = ("BENEFICIAL_OWNER", "DIRECTOR", "AUTHORISED_PERSON")

SUBMIT_POLL_ATTEMPTS = 15
SUBMIT_POLL_INTERVAL_S = 2.0


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
    address = {
        "address_line1": spec.address_line1,
        "suburb": spec.city,
        "state": spec.state,
        "postcode": spec.postcode,
        "country_code": spec.country_code,
    }
    return {
        "nickname": spec.nickname,
        "primary_contact": {"email": spec.email},
        "account_details": {
            "legal_entity_type": "BUSINESS",
            "business_details": {
                "business_name": spec.business_name,
                "business_structure": SELLER_BUSINESS_STRUCTURE,
                "description_of_goods_or_services": SELLER_GOODS_DESCRIPTION,
                "industry_category_code": SELLER_INDUSTRY_CODE,
                "operating_country": [spec.country_code],
                "account_usage": {
                    "product_reference": list(SELLER_PRODUCTS),
                    "estimated_monthly_revenue": {
                        "currency": "USD",
                        "amount": SELLER_MONTHLY_REVENUE_USD,
                    },
                },
                "business_identifiers": [
                    {"country_code": spec.country_code, "number": spec.ein, "type": "EIN"}
                ],
                "registration_address": address,
            },
            "business_person_details": [_owner(spec, address)],
        },
        "customer_agreements": {
            # Submit rejects the account unless both agreements are true.
            "agreed_to_data_usage": True,
            "agreed_to_terms_and_conditions": True,
            "terms_and_conditions": {
                "agreed_at": agreed_at.isoformat(),
                "service_agreement_type": "FULL",
            },
        },
    }


def _owner(spec: ConnectedAccountSpec, address: dict[str, str]) -> dict[str, Any]:
    """Sole owner/director of a demo seller (test identity, sandbox only)."""
    return {
        "first_name": "Sandbox",
        "last_name": "Owner",
        "date_of_birth": SELLER_OWNER_DOB,
        "email": spec.email,
        "nationality": spec.country_code,
        "job_title": "Owner",
        "roles": list(SELLER_OWNER_ROLES),
        "ownership_percentage": 100,
        "residential_address": address,
        "identifications": {
            "primary": {
                "identification_type": "PASSPORT",
                "issuing_country_code": spec.country_code,
                "passport": {"number": SELLER_OWNER_PASSPORT},
            }
        },
    }


class SandboxAdmin:
    def __init__(
        self,
        client: RestAirwallexClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if "sandbox" not in client.base_url:
            raise ValueError("SandboxAdmin only runs against the Airwallex sandbox")
        self._client = client
        self._sleep = sleep

    def onboard_seller(self, spec: ConnectedAccountSpec) -> str:
        """create -> submit -> simulate ACTIVE -> confirm. Returns the connected account id."""
        body = build_account_body(spec, datetime.now(UTC))
        account_id = str(self._client.request("POST", f"{API}/accounts/create", json=body)["id"])
        self._client.request("POST", f"{API}/accounts/{account_id}/submit")
        self._wait_until_submitted(account_id)
        self._client.request(
            "POST",
            f"{API}/simulation/accounts/{account_id}/update_status",
            json={"next_status": "ACTIVE", "force": True},
        )
        status = self._client.request("GET", f"{API}/accounts/{account_id}").get("status")
        if status != "ACTIVE":
            raise AirwallexAPIError(200, "account_not_active", f"{account_id} is {status}")
        return account_id

    def _wait_until_submitted(self, account_id: str) -> None:
        """Submit is asynchronous; the activation simulation rejects a CREATED account."""
        for attempt in range(SUBMIT_POLL_ATTEMPTS):
            status = self._client.request("GET", f"{API}/accounts/{account_id}").get("status")
            if status != "CREATED":
                return
            if attempt < SUBMIT_POLL_ATTEMPTS - 1:
                self._sleep(SUBMIT_POLL_INTERVAL_S)
        raise AirwallexAPIError(200, "account_not_submitted", f"{account_id} still CREATED")

    def create_platform_report(
        self,
        report_type: str,
        from_created_at: str | None = None,
        to_created_at: str | None = None,
    ) -> str:
        """POST /platform_reports/create (CSV). ``to_created_at`` is exclusive; max 366 days.
        Balance and account reports are snapshots and take no date range."""
        if report_type not in REPORT_TYPES:
            raise ValueError(f"unknown report type {report_type!r}")
        dates = {"from_created_at": from_created_at, "to_created_at": to_created_at}
        given = {name: value for name, value in dates.items() if value is not None}
        if given and report_type in UNDATED_REPORT_TYPES:
            raise ValueError(f"{report_type} does not accept a date range")
        body = {"type": report_type, "file_format": "CSV", **given}
        return str(self._client.request("POST", f"{API}/platform_reports/create", json=body)["id"])

    def get_platform_report(self, report_id: str) -> PlatformReport:
        """download_url appears only when COMPLETED and expires one minute later."""
        data = self._client.request("GET", f"{API}/platform_reports/{report_id}")
        return PlatformReport(
            report_id=report_id,
            status=str(data.get("status")),
            download_url=data.get("download_url"),
        )
