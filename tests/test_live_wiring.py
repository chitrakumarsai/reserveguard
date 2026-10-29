"""Wiring the real Airwallex client: seller account file, service factory, API auth."""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
from reserveguard.models import Tier
from reserveguard.scenario import (
    CACHED_SELLERS_WARNING,
    DEMO_GROSS_CENTS,
    DEMO_SELLERS,
    NEW_SELLER_MAX_DAYS,
    TRUSTED_SELLER_MIN_DAYS,
    SellerFile,
    build_service,
    demo_seller_specs,
    fetch_accounts,
    load_seller_accounts,
    load_seller_file,
    onboard_demo_sellers,
    save_seller_file,
    sellers_from_accounts,
    tier_for_age,
)
from reserveguard.settlement import SettlementService
from reserveguard.store import SqliteStore

pytestmark = pytest.mark.integration

BASE = "https://api.sandbox.airwallex.com"
ACCOUNTS = {"A": "acct_live_a", "B": "acct_live_b", "C": "acct_live_c"}
TIERS = {"A": Tier.TRUSTED, "B": Tier.STANDARD}
HIDDEN = "acct_leftover"
SELLER_FILE = SellerFile(accounts=ACCOUNTS, tiers=TIERS, hidden=frozenset({HIDDEN}))
NOW = datetime(2026, 10, 29, tzinfo=UTC)
EXAMPLE_PATH = Path(__file__).resolve().parents[1] / "data" / "sandbox_sellers.example.json"
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
    save_seller_file(path, SELLER_FILE)
    return path


def _account(
    account_id: str, nickname: str = "", status: str = "ACTIVE", days_old: int = 2
) -> dict[str, Any]:
    created = NOW - timedelta(days=days_old)
    return {
        "id": account_id,
        "status": status,
        "nickname": nickname,
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%S+0000"),
        "account_details": {"business_details": {"business_name": "Sandbox Business"}},
    }


LIVE_ACCOUNTS = [
    _account("acct_live_a", "ReserveGuard Seller A"),
    _account("acct_live_b", "ReserveGuard Seller B"),
    _account("acct_live_c", "ReserveGuard Seller C"),
]


def _mock_accounts(api: respx.MockRouter, *pages: list[dict[str, Any]]) -> respx.Route:
    responses = [
        respx.MockResponse(200, json={"items": items, "has_more": n < len(pages) - 1})
        for n, items in enumerate(pages)
    ]
    return api.get("/api/v1/accounts").mock(side_effect=responses)


def _mock_balance(api: respx.MockRouter, cents: int = 1_000_000_000) -> respx.Route:
    route = api.get("/api/v1/balances/current")
    route.respond(200, json=[{"currency": "USD", "available_amount": cents // 100}])
    return route


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
def test_seller_file_round_trip(sellers_file: Path) -> None:
    assert load_seller_file(sellers_file) == SELLER_FILE
    assert load_seller_accounts(sellers_file) == ACCOUNTS


def test_seller_file_still_reads_the_plain_account_format(tmp_path: Path) -> None:
    path = tmp_path / "old.json"
    path.write_text(json.dumps(ACCOUNTS))
    assert load_seller_file(path) == SellerFile(accounts=ACCOUNTS)


def test_example_seller_file_is_valid() -> None:
    example = load_seller_file(EXAMPLE_PATH)
    assert set(example.accounts) == {"A", "B", "C"}
    assert example.tiers == TIERS


def test_seller_file_rejects_an_unknown_tier(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    sellers = {sid: {"account_id": acct} for sid, acct in ACCOUNTS.items()}
    path.write_text(json.dumps({"sellers": {**sellers, "A": {**sellers["A"], "tier": "gold"}}}))
    with pytest.raises(ValueError, match="tier"):
        load_seller_file(path)


def test_seller_file_rejects_one_account_for_two_sellers(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**ACCOUNTS, "B": ACCOUNTS["A"]}))
    with pytest.raises(ValueError, match="more than one"):
        load_seller_file(path)


def test_seller_file_rejects_hiding_a_mapped_seller(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    sellers = {sid: {"account_id": acct} for sid, acct in ACCOUNTS.items()}
    path.write_text(json.dumps({"sellers": sellers, "hidden": [ACCOUNTS["C"]]}))
    with pytest.raises(ValueError, match="hidden"):
        load_seller_file(path)


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
# --- tiers and sellers from Airwallex accounts ----------------------------------------
@pytest.mark.parametrize(
    ("days_old", "tier"),
    [
        (0, Tier.NEW),
        (NEW_SELLER_MAX_DAYS - 1, Tier.NEW),
        (NEW_SELLER_MAX_DAYS, Tier.STANDARD),
        (TRUSTED_SELLER_MIN_DAYS - 1, Tier.STANDARD),
        (TRUSTED_SELLER_MIN_DAYS, Tier.TRUSTED),
    ],
)
def test_tier_follows_account_age(days_old: int, tier: Tier) -> None:
    assert tier_for_age(NOW - timedelta(days=days_old), NOW) is tier


def test_sellers_keep_their_file_ids_and_tier_overrides() -> None:
    sellers = sellers_from_accounts(LIVE_ACCOUNTS, SELLER_FILE, NOW)
    assert [(s.seller_id, s.name, s.tier, s.account_id) for s in sellers] == [
        ("A", "ReserveGuard Seller A", Tier.TRUSTED, "acct_live_a"),
        ("B", "ReserveGuard Seller B", Tier.STANDARD, "acct_live_b"),
        ("C", "ReserveGuard Seller C", Tier.NEW, "acct_live_c"),
    ]


def test_unmapped_active_account_is_a_seller_tiered_by_age() -> None:
    old = _account("acct_old", "Old Shop", days_old=400)
    sellers = sellers_from_accounts([*LIVE_ACCOUNTS, old], SELLER_FILE, NOW)
    added = next(s for s in sellers if s.account_id == "acct_old")
    assert (added.seller_id, added.name, added.tier) == ("acct_old", "Old Shop", Tier.TRUSTED)


def test_hidden_and_inactive_accounts_are_skipped() -> None:
    extra = [_account(HIDDEN, "ReserveGuard Seller A"), _account("acct_x", status="SUBMITTED")]
    sellers = sellers_from_accounts([*LIVE_ACCOUNTS, *extra], SELLER_FILE, NOW)
    assert [s.seller_id for s in sellers] == ["A", "B", "C"]


def test_seller_name_falls_back_to_business_name() -> None:
    nameless = [{**a, "nickname": ""} for a in LIVE_ACCOUNTS]
    assert {s.name for s in sellers_from_accounts(nameless, SELLER_FILE, NOW)} == {
        "Sandbox Business"
    }


def test_a_mapped_seller_missing_from_airwallex_is_an_error() -> None:
    with pytest.raises(ValueError, match="C"):
        sellers_from_accounts(LIVE_ACCOUNTS[:2], SELLER_FILE, NOW)


@pytest.mark.parametrize(
    "bad",
    [{"id": "nope"}, {"id": "acct_q", "status": None}, {"id": "acct_q", "created_at": "x"}],
)
def test_malformed_account_is_rejected(bad: dict[str, Any]) -> None:
    item = {**_account("acct_q"), **bad}
    with pytest.raises(ValueError, match="account"):
        sellers_from_accounts([*LIVE_ACCOUNTS, item], SELLER_FILE, NOW)


# --- service factory ----------------------------------------------------------------
def test_build_service_uses_fake_by_default(sellers_file: Path) -> None:
    service, client, warning = build_service(_settings(), sellers_file)
    assert isinstance(client, FakeAirwallexClient)
    assert {s.account_id for s in service.sellers} == {s.account_id for s in DEMO_SELLERS}
    assert warning is None


def test_fetch_accounts_reads_a_full_page_then_the_last_page(api: respx.MockRouter) -> None:
    # Airwallex's minimum page_size is 10, so a real second page needs 11+ accounts
    accounts = [_account(f"acct_{n:02d}", f"Shop {n}") for n in range(11)]
    pages = [(accounts[:10], True), (accounts[10:], False)]
    route = api.get("/api/v1/accounts").mock(
        side_effect=[
            respx.MockResponse(200, json={"items": items, "has_more": more, "total_count": 11})
            for items, more in pages
        ]
    )

    found = fetch_accounts(RestAirwallexClient(_settings(use_fake_airwallex=False)))

    assert [a["id"] for a in found] == [a["id"] for a in accounts]
    assert [call.request.url.params["page_num"] for call in route.calls] == ["0", "1"]


def test_build_service_live_loads_sellers_from_airwallex(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path
) -> None:
    _mock_balance(api)
    extra = _account("acct_new_shop", "New Shop")
    route = _mock_accounts(api, LIVE_ACCOUNTS[:2], [LIVE_ACCOUNTS[2], extra])
    settings = _settings(use_fake_airwallex=False, reserveguard_db_path=tmp_path / "rg.db")

    service, client, warning = build_service(settings, sellers_file)

    assert isinstance(client, RestAirwallexClient)
    assert warning is None
    assert [route.calls[n].request.url.params["page_num"] for n in range(2)] == ["0", "1"]
    assert {s.seller_id: s.account_id for s in service.sellers} == {
        **ACCOUNTS,
        "acct_new_shop": "acct_new_shop",
    }
    assert [s.tier for s in service.sellers][:3] == [Tier.TRUSTED, Tier.STANDARD, Tier.NEW]


def test_live_falls_back_to_the_seller_file_when_airwallex_is_down(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_balance(api)
    api.get("/api/v1/accounts").respond(503)
    settings = _settings(use_fake_airwallex=False, reserveguard_db_path=tmp_path / "rg.db")

    with caplog.at_level(logging.WARNING):
        service, _, warning = build_service(settings, sellers_file)

    assert warning == CACHED_SELLERS_WARNING
    assert "cached sellers" in caplog.text
    assert [(s.seller_id, s.tier) for s in service.sellers] == [
        ("A", Tier.TRUSTED),
        ("B", Tier.STANDARD),
        ("C", Tier.NEW),
    ]


def test_live_falls_back_to_the_sellers_of_the_saved_ledger(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path
) -> None:
    _mock_balance(api)
    accounts = api.get("/api/v1/accounts")
    old = _account("acct_old", "Old Shop", days_old=400)
    accounts.respond(200, json={"items": [*LIVE_ACCOUNTS, old], "has_more": False})
    settings = _settings(use_fake_airwallex=False, reserveguard_db_path=tmp_path / "rg.db")
    first, _, _ = build_service(settings, sellers_file)
    accounts.respond(200, json={"items": "not a list"})

    service, _, warning = build_service(settings, sellers_file)

    assert warning == CACHED_SELLERS_WARNING
    assert service.sellers == first.sellers


def test_live_service_keeps_its_ledger_in_sqlite(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path
) -> None:
    db = tmp_path / "live.db"
    balance = _mock_balance(api)
    api.get("/api/v1/accounts").respond(200, json={"items": LIVE_ACCOUNTS, "has_more": False})
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
    assert load_seller_file(path) == SellerFile(accounts=accounts, tiers={**TIERS, "C": Tier.NEW})


def test_onboarding_failure_keeps_sellers_already_created(
    api: respx.MockRouter, tmp_path: Path
) -> None:
    _mock_onboarding(api, ["acct_1", "acct_2", "FAIL"])
    path = tmp_path / "sellers.json"
    with pytest.raises(AirwallexAPIError):
        onboard_demo_sellers(_admin(), path, "x")
    assert json.loads(path.read_text())["sellers"] == {
        "A": {"account_id": "acct_1", "tier": "trusted"},
        "B": {"account_id": "acct_2", "tier": "standard"},
    }
    assert json.loads(path.read_text())["hidden"] == []


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


def test_info_shows_the_cached_sellers_warning(
    api: respx.MockRouter, sellers_file: Path, tmp_path: Path
) -> None:
    _mock_balance(api)
    api.get("/api/v1/accounts").respond(503)
    settings = _settings(use_fake_airwallex=False, reserveguard_db_path=tmp_path / "rg.db")
    client = TestClient(create_app(settings, sellers_path=sellers_file))
    assert client.get("/info", headers=AUTH).json()["seller_warning"] == CACHED_SELLERS_WARNING


def test_info_has_no_warning_in_fake_mode() -> None:
    client = TestClient(create_app(_settings()))
    assert client.get("/info", headers=AUTH).json()["seller_warning"] is None


def test_api_live_mode_requires_seller_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sandbox_setup.py"):
        create_app(_settings(use_fake_airwallex=False), sellers_path=tmp_path / "none.json")
