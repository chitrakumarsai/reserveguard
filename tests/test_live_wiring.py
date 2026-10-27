"""Wiring the real Airwallex client: seller account file, service factory, API auth."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient

from reserveguard.airwallex.client import AirwallexAPIError
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.airwallex.sandbox import SandboxAdmin
from reserveguard.api import create_app
from reserveguard.config import Settings
from reserveguard.scenario import (
    DEMO_GROSS_CENTS,
    DEMO_SELLERS,
    build_service,
    demo_seller_specs,
    load_seller_accounts,
    onboard_demo_sellers,
    save_seller_accounts,
)
from reserveguard.settlement import SettlementService
from reserveguard.store import SqliteStore

pytestmark = pytest.mark.integration

BASE = "https://api.sandbox.airwallex.com"
ACCOUNTS = {"A": "acct_live_a", "B": "acct_live_b", "C": "acct_live_c"}
TOKEN = "test-token"  # noqa: S105 - test fixture, not a credential
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "airwallex_client_id": "cid",
        "airwallex_api_key": "key",
        "reserveguard_api_token": TOKEN,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.post("/api/v1/authentication/login").respond(
            201, json={"token": "t", "expires_at": "2099-01-01T00:00:00+0000"}
        )
        yield router


@pytest.fixture
def sellers_file(tmp_path: Path) -> Path:
    path = tmp_path / "sandbox_sellers.json"
    save_seller_accounts(path, ACCOUNTS)
    return path


# --- reconciliation against a platform account that holds other money -------------
def test_reconciliation_ignores_money_already_in_the_platform_account() -> None:
    float_cents = 1_000_000_000  # e.g. the sandbox's pre-loaded $10M
    fake = FakeAirwallexClient({None: float_cents + sum(DEMO_GROSS_CENTS.values())})
    service = SettlementService(fake, DEMO_SELLERS)
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    assert all(r.passed for r in service.reconciliation())


def test_reconciliation_still_catches_unexplained_platform_movement() -> None:
    fake = FakeAirwallexClient({None: 2_000_000})
    service = SettlementService(fake, DEMO_SELLERS)
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    fake.transfer_to_seller("acct_seller_a", 1, "USD", "stray", "stray-key")
    checks = {r.name: r for r in service.reconciliation()}
    assert checks["platform_balance"].passed is False


# --- seller account file ------------------------------------------------------------
def test_seller_accounts_round_trip(sellers_file: Path) -> None:
    assert load_seller_accounts(sellers_file) == ACCOUNTS


def test_missing_seller_file_points_to_setup_script(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sandbox_setup.py"):
        load_seller_accounts(tmp_path / "nope.json")


def test_seller_file_must_cover_every_demo_seller(tmp_path: Path) -> None:
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({"A": "acct_a", "B": "acct_b"}))
    with pytest.raises(ValueError, match="C"):
        load_seller_accounts(path)


def test_seller_file_rejects_non_account_ids(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**ACCOUNTS, "C": "not-an-account"}))
    with pytest.raises(ValueError, match="acct_"):
        load_seller_accounts(path)


def test_demo_seller_specs_are_unique_sandbox_businesses() -> None:
    specs = demo_seller_specs("20261020")
    assert set(specs) == {"A", "B", "C"}
    assert {s.business_name for s in specs.values()} == {"Sandbox Business"}
    assert len({s.email for s in specs.values()}) == 3


# --- service factory ----------------------------------------------------------------
def test_build_service_uses_fake_by_default(sellers_file: Path) -> None:
    service, client = build_service(_settings(), sellers_file)
    assert isinstance(client, FakeAirwallexClient)
    assert {s.account_id for s in service.sellers} == {s.account_id for s in DEMO_SELLERS}


def test_build_service_live_maps_real_account_ids(
    api: respx.MockRouter, sellers_file: Path
) -> None:
    api.get("/api/v1/balances/current").respond(
        200, json=[{"currency": "USD", "available_amount": 10_000_000}]
    )
    service, client = build_service(_settings(use_fake_airwallex=False), sellers_file)
    assert isinstance(client, RestAirwallexClient)
    assert {s.seller_id: s.account_id for s in service.sellers} == ACCOUNTS
    assert [s.tier for s in service.sellers] == [s.tier for s in DEMO_SELLERS]


def test_live_service_keeps_its_ledger_in_sqlite(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path
) -> None:
    db = tmp_path / "live.db"
    balance = api.get("/api/v1/balances/current")
    balance.respond(200, json=[{"currency": "USD", "available_amount": 10_000_000}])
    build_service(_settings(use_fake_airwallex=False, reserveguard_db_path=db), sellers_file)
    balance.respond(200, json=[{"currency": "USD", "available_amount": 12_345}])

    build_service(_settings(use_fake_airwallex=False, reserveguard_db_path=db), sellers_file)

    stored = SqliteStore(db).load()
    assert stored is not None
    assert stored.opening_balance_cents == 1_000_000_000  # from the first start, not re-read
    assert dict(stored.accounts) == ACCOUNTS


# --- sandbox onboarding of several sellers -------------------------------------------
def _mock_onboarding(api: respx.MockRouter, ids: list[str]) -> respx.Route:
    created = iter(ids)

    def create(_req: object) -> respx.MockResponse:
        account_id = next(created)
        if account_id == "FAIL":
            return respx.MockResponse(400, json={"code": "invalid_argument", "message": "no"})
        return respx.MockResponse(201, json={"id": account_id})

    route = api.post("/api/v1/accounts/create").mock(side_effect=create)
    api.post(url__regex=r"/api/v1/accounts/acct_\d/submit").respond(200, json={})
    api.post(url__regex=r"/api/v1/simulation/accounts/acct_\d/update_status").respond(200)
    api.get(url__regex=r"/api/v1/accounts/acct_\d$").respond(200, json={"status": "ACTIVE"})
    return route


def _admin() -> SandboxAdmin:
    return SandboxAdmin(
        RestAirwallexClient(_settings(), sleep=lambda _s: None), sleep=lambda _s: None
    )


def test_onboard_demo_sellers_saves_every_account(api: respx.MockRouter, tmp_path: Path) -> None:
    _mock_onboarding(api, ["acct_1", "acct_2", "acct_3"])
    path = tmp_path / "sellers.json"
    accounts = onboard_demo_sellers(_admin(), path, "x")
    assert accounts == {"A": "acct_1", "B": "acct_2", "C": "acct_3"}
    assert load_seller_accounts(path) == accounts


def test_onboarding_failure_keeps_sellers_already_created(
    api: respx.MockRouter, tmp_path: Path
) -> None:
    _mock_onboarding(api, ["acct_1", "acct_2", "FAIL"])
    path = tmp_path / "sellers.json"
    with pytest.raises(AirwallexAPIError):
        onboard_demo_sellers(_admin(), path, "x")
    assert json.loads(path.read_text()) == {"A": "acct_1", "B": "acct_2"}


def test_onboarding_resumes_only_missing_sellers(api: respx.MockRouter, tmp_path: Path) -> None:
    path = tmp_path / "sellers.json"
    path.write_text(json.dumps({"A": "acct_1", "B": "acct_2"}))
    create = _mock_onboarding(api, ["acct_3"])
    assert onboard_demo_sellers(_admin(), path, "x")["C"] == "acct_3"
    assert create.call_count == 1


# --- API authentication ---------------------------------------------------------------
def test_health_needs_no_token() -> None:
    client = TestClient(create_app(_settings(reserveguard_api_token="")))
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("path", ["/sellers", "/reconciliation"])
def test_reads_need_a_token(path: str) -> None:
    client = TestClient(create_app(_settings()))
    assert client.get(path).status_code == 401
    assert client.get(path, headers=AUTH).status_code == 200


def test_settle_rejects_missing_and_wrong_tokens() -> None:
    client = TestClient(create_app(_settings()))
    body = {"cycle_id": "c1", "gross_cents": {"A": 100}}
    assert client.post("/settle", json=body).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/settle", json=body, headers=wrong).status_code == 401
    assert client.post("/settle", json=body, headers=AUTH).status_code == 200


def test_api_fails_closed_without_a_configured_token() -> None:
    client = TestClient(create_app(_settings(reserveguard_api_token="")))
    response = client.get("/sellers", headers={"Authorization": "Bearer "})
    assert response.status_code == 503


def test_api_live_mode_requires_seller_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sandbox_setup.py"):
        create_app(_settings(use_fake_airwallex=False), sellers_path=tmp_path / "none.json")
