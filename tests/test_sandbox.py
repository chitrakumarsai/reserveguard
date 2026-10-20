"""Sandbox-only setup helpers: funding, seller onboarding, platform reports."""

import json
from collections.abc import Iterator

import pytest
import respx

from reserveguard.airwallex.client import AirwallexAPIError
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.airwallex.sandbox import ConnectedAccountSpec, SandboxAdmin
from reserveguard.config import Settings

pytestmark = pytest.mark.unit

BASE = "https://api.sandbox.airwallex.com"
SPEC = ConnectedAccountSpec(
    nickname="Seller C",
    business_name="Sandbox Business",
    ein="12-3456789",
    email="seller-c@example.com",
    address_line1="1 Market St",
    city="San Francisco",
    state="CA",
    postcode="94105",
)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.post("/api/v1/authentication/login").respond(
            201, json={"token": "t", "expires_at": "2099-01-01T00:00:00+0000"}
        )
        yield router


@pytest.fixture
def admin() -> SandboxAdmin:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    return SandboxAdmin(RestAirwallexClient(settings, sleep=lambda _s: None))


def test_refuses_non_sandbox_base_url() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
        airwallex_base_url="https://api.airwallex.com",
    )
    with pytest.raises(ValueError, match="sandbox"):
        SandboxAdmin(RestAirwallexClient(settings))


def test_reuses_existing_global_account(api: respx.MockRouter, admin: SandboxAdmin) -> None:
    api.get("/api/v1/global_accounts").respond(
        200,
        json={
            "items": [
                {"id": "ga_eur", "status": "ACTIVE", "required_features": [{"currency": "EUR"}]},
                {"id": "ga_usd", "status": "ACTIVE", "required_features": [{"currency": "USD"}]},
            ]
        },
    )
    create = api.post("/api/v1/global_accounts/create")
    assert admin.ensure_global_account("USD", "US") == "ga_usd"
    assert create.call_count == 0


def test_creates_global_account_when_missing(api: respx.MockRouter, admin: SandboxAdmin) -> None:
    api.get("/api/v1/global_accounts").respond(200, json={"items": []})
    create = api.post("/api/v1/global_accounts/create").respond(201, json={"id": "ga_new"})
    assert admin.ensure_global_account("USD", "US") == "ga_new"
    body = json.loads(create.calls[0].request.content)
    assert body["required_features"] == [{"currency": "USD", "transfer_method": "LOCAL"}]
    assert body["country_code"] == "US" and body["request_id"]


def test_simulate_deposit_sends_major_units(api: respx.MockRouter, admin: SandboxAdmin) -> None:
    route = api.post("/api/v1/simulation/deposit/create").respond(201, json={"id": "dep_1"})
    assert admin.simulate_deposit("ga_usd", 1_000_000, "USD") == "dep_1"
    body = json.loads(route.calls[0].request.content)
    assert body["amount"] == 10000.0 and body["global_account_id"] == "ga_usd"


def test_onboard_seller_runs_create_submit_activate(
    api: respx.MockRouter, admin: SandboxAdmin
) -> None:
    create = api.post("/api/v1/accounts/create").respond(201, json={"id": "acct_c"})
    submit = api.post("/api/v1/accounts/acct_c/submit").respond(200, json={"id": "acct_c"})
    activate = api.post("/api/v1/simulation/accounts/acct_c/update_status").respond(200, json={})
    api.get("/api/v1/accounts/acct_c").respond(200, json={"id": "acct_c", "status": "ACTIVE"})

    assert admin.onboard_seller(SPEC) == "acct_c"

    created = json.loads(create.calls[0].request.content)
    business = created["account_details"]["business_details"]
    assert business["business_identifiers"] == [
        {"country_code": "US", "number": "12-3456789", "type": "EIN"}
    ]
    assert created["nickname"] == "Seller C"
    assert submit.call_count == 1
    assert json.loads(activate.calls[0].request.content) == {"next_status": "ACTIVE", "force": True}


def test_onboard_seller_fails_if_not_active(api: respx.MockRouter, admin: SandboxAdmin) -> None:
    api.post("/api/v1/accounts/create").respond(201, json={"id": "acct_c"})
    api.post("/api/v1/accounts/acct_c/submit").respond(200, json={})
    api.post("/api/v1/simulation/accounts/acct_c/update_status").respond(200, json={})
    api.get("/api/v1/accounts/acct_c").respond(200, json={"status": "ACTION_REQUIRED"})
    with pytest.raises(AirwallexAPIError, match="ACTION_REQUIRED"):
        admin.onboard_seller(SPEC)


def test_platform_report_create_and_fetch(api: respx.MockRouter, admin: SandboxAdmin) -> None:
    create = api.post("/api/v1/platform_reports/create").respond(201, json={"id": "rep_1"})
    api.get("/api/v1/platform_reports/rep_1").respond(
        200, json={"id": "rep_1", "status": "COMPLETED", "download_url": "https://x/r.csv"}
    )
    report_id = admin.create_platform_report(
        "TRANSACTION_RECON_REPORT", "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z"
    )
    assert report_id == "rep_1"
    assert json.loads(create.calls[0].request.content) == {
        "type": "TRANSACTION_RECON_REPORT",
        "file_format": "CSV",
        "from_created_at": "2026-10-01T00:00:00Z",
        "to_created_at": "2026-11-01T00:00:00Z",
    }
    report = admin.get_platform_report("rep_1")
    assert (report.status, report.download_url) == ("COMPLETED", "https://x/r.csv")


def test_platform_report_rejects_unknown_type(admin: SandboxAdmin) -> None:
    with pytest.raises(ValueError, match="report type"):
        admin.create_platform_report("NOPE", "a", "b")
