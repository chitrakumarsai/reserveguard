"""RestAirwallexClient against a mocked Airwallex API (respx). No real network."""

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from reserveguard.airwallex.client import (
    AirwallexAPIError,
    AirwallexTransientError,
    InsufficientFundsError,
)
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.config import Settings

pytestmark = pytest.mark.unit

BASE = "https://api.sandbox.airwallex.com"
LOGIN = f"{BASE}/api/v1/authentication/login"
TRANSFERS = f"{BASE}/api/v1/connected_account_transfers"
CHARGES = f"{BASE}/api/v1/charges"
TOKEN_BODY = {"token": "tok-1", "expires_at": "2026-10-20T12:30:00+0000"}
NOW = 1_792_497_600.0  # 2026-10-20T12:00:00Z


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.post("/api/v1/authentication/login").respond(201, json=TOKEN_BODY)
        yield router


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock: Clock) -> RestAirwallexClient:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    return RestAirwallexClient(settings, clock=clock, sleep=lambda _s: None)


def _body(route: respx.Route, call: int = -1) -> dict[str, object]:
    request = route.calls[call].request
    return dict(json.loads(request.content))


def test_requires_credentials() -> None:
    with pytest.raises(ValueError, match="AIRWALLEX_CLIENT_ID"):
        RestAirwallexClient(Settings(_env_file=None))  # type: ignore[call-arg]


def test_login_sends_credentials_and_token_is_reused(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    balances = api.get("/api/v1/balances/current").respond(200, json=[])
    client.get_balance(None)
    client.get_balance(None)
    login = api.routes[0]
    assert login.call_count == 1
    assert login.calls[0].request.headers["x-client-id"] == "cid"
    assert login.calls[0].request.headers["x-api-key"] == "key"
    assert balances.calls[0].request.headers["authorization"] == "Bearer tok-1"


def test_token_refreshed_near_expiry(
    api: respx.MockRouter, client: RestAirwallexClient, clock: Clock
) -> None:
    api.get("/api/v1/balances/current").respond(200, json=[])
    client.get_balance(None)
    clock.now += 29 * 60 + 30  # inside the refresh margin
    client.get_balance(None)
    assert api.routes[0].call_count == 2


def test_401_triggers_one_relogin(api: respx.MockRouter, client: RestAirwallexClient) -> None:
    api.get("/api/v1/balances/current").mock(
        side_effect=[
            httpx.Response(401, json={"code": "credentials_expired"}),
            httpx.Response(200, json=[]),
        ]
    )
    assert client.get_balance(None) == 0
    assert api.routes[0].call_count == 2


def test_seller_balance_uses_on_behalf_of_and_converts_to_cents(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    route = api.get("/api/v1/balances/current").respond(
        200,
        json=[
            {"account_type": "cash", "currency": "AUD", "available_amount": 9},
            {"account_type": "cash", "currency": "USD", "available_amount": 500.25},
        ],
    )
    assert client.get_balance("acct_c", currency="USD") == 50_025
    assert route.calls[0].request.headers["x-on-behalf-of"] == "acct_c"


def test_platform_balance_has_no_on_behalf_of(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    route = api.get("/api/v1/balances/current").respond(200, json=[])
    client.get_balance(None)
    assert "x-on-behalf-of" not in route.calls[0].request.headers


def test_transfer_sends_major_units_and_request_id(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    route = api.post("/api/v1/connected_account_transfers/create").respond(
        201, json={"id": "tr_1", "status": "NEW"}
    )
    assert client.transfer_to_seller("acct_c", 255_000, "USD", "settle:c1:C", "key-1") == "tr_1"
    assert _body(route) == {
        "request_id": "key-1",
        "amount": "2550.00",
        "currency": "USD",
        "destination": "acct_c",
        "reason": "goods_purchased",
        "reference": "settle:c1:C",
    }


def test_charge_uses_source(api: respx.MockRouter, client: RestAirwallexClient) -> None:
    route = api.post("/api/v1/charges/create").respond(201, json={"id": "ch_1", "status": "NEW"})
    assert client.charge_seller("acct_c", 50_000, "USD", "reserve:C", "key-2") == "ch_1"
    body = _body(route)
    assert (body["source"], body["amount"], body["request_id"]) == ("acct_c", "500.00", "key-2")


def test_rejects_bad_reference(client: RestAirwallexClient) -> None:
    with pytest.raises(ValueError, match="reference"):
        client.transfer_to_seller("acct_c", 1, "USD", "x" * 141, "k")


def test_insufficient_fund_maps_to_domain_error(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").respond(
        400, json={"code": "insufficient_fund", "message": "Insufficient fund"}
    )
    with pytest.raises(InsufficientFundsError):
        client.charge_seller("acct_c", 1, "USD", "r", "k")


def test_other_4xx_is_api_error_with_code(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").respond(
        400, json={"code": "invalid_argument", "source": "reason", "message": "bad"}
    )
    with pytest.raises(AirwallexAPIError) as info:
        client.charge_seller("acct_c", 1, "USD", "r", "k")
    assert (info.value.status_code, info.value.code) == (400, "invalid_argument")


def test_5xx_and_timeouts_are_transient(api: respx.MockRouter, client: RestAirwallexClient) -> None:
    api.post("/api/v1/charges/create").mock(
        side_effect=[httpx.Response(503), httpx.ConnectTimeout("slow")]
    )
    for key in ("k1", "k2"):
        with pytest.raises(AirwallexTransientError):
            client.charge_seller("acct_c", 1, "USD", "r", key)


def test_429_backs_off_then_succeeds(clock: Clock) -> None:
    sleeps: list[float] = []
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    client = RestAirwallexClient(settings, clock=clock, sleep=sleeps.append)
    with respx.mock(base_url=BASE) as api:
        api.post("/api/v1/authentication/login").respond(201, json=TOKEN_BODY)
        api.get("/api/v1/balances/current").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "2"}),
                httpx.Response(200, json=[]),
            ]
        )
        assert client.get_balance(None) == 0
    assert sleeps == [2.0]


def test_429_gives_up_as_transient(api: respx.MockRouter, client: RestAirwallexClient) -> None:
    api.get("/api/v1/balances/current").respond(429)
    with pytest.raises(AirwallexTransientError, match="rate limited"):
        client.get_balance(None)


def test_retry_after_timeout_finds_existing_transfer_instead_of_resending(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    create = api.post("/api/v1/connected_account_transfers/create").mock(
        side_effect=httpx.ReadTimeout("lost response")
    )
    lookup = api.get("/api/v1/connected_account_transfers").respond(
        200, json={"has_more": False, "items": [{"id": "tr_9", "request_id": "key-1"}]}
    )
    with pytest.raises(AirwallexTransientError):
        client.transfer_to_seller("acct_c", 100, "USD", "r", "key-1")
    assert client.transfer_to_seller("acct_c", 100, "USD", "r", "key-1") == "tr_9"
    assert create.call_count == 1
    assert lookup.calls[0].request.url.params["request_id"] == "key-1"


def test_retry_after_timeout_resends_when_nothing_was_created(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").mock(
        side_effect=[httpx.ReadTimeout("lost"), httpx.Response(201, json={"id": "ch_2"})]
    )
    api.get("/api/v1/charges").respond(200, json={"has_more": False, "items": []})
    with pytest.raises(AirwallexTransientError):
        client.charge_seller("acct_c", 100, "USD", "r", "key-3")
    assert client.charge_seller("acct_c", 100, "USD", "r", "key-3") == "ch_2"


def test_duplicate_request_id_returns_existing_object(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").respond(400, json={"code": "request_id_duplicate"})
    api.get("/api/v1/charges").respond(200, json={"items": [{"id": "ch_7", "request_id": "k"}]})
    assert client.charge_seller("acct_c", 100, "USD", "r", "k") == "ch_7"


def test_duplicate_request_id_without_match_is_an_error(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").respond(400, json={"code": "request_id_duplicate"})
    api.get("/api/v1/charges").respond(200, json={"items": []})
    with pytest.raises(AirwallexAPIError, match="request_id_duplicate"):
        client.charge_seller("acct_c", 100, "USD", "r", "k")


def test_get_status_for_known_and_unknown_objects(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.post("/api/v1/charges/create").respond(201, json={"id": "ch_1", "status": "NEW"})
    api.get("/api/v1/charges/ch_1").respond(200, json={"id": "ch_1", "status": "SETTLED"})
    api.get("/api/v1/connected_account_transfers/tr_x").respond(404, json={"code": "not_found"})
    api.get("/api/v1/charges/tr_x").respond(404, json={"code": "not_found"})
    api.get("/api/v1/connected_account_transfers/tr_y").respond(
        200, json={"id": "tr_y", "status": "PENDING"}
    )
    client.charge_seller("acct_c", 1, "USD", "r", "k")
    assert client.get_status("ch_1") == "SETTLED"
    assert client.get_status("tr_y") == "PENDING"
    with pytest.raises(AirwallexAPIError):
        client.get_status("tr_x")


def test_unknown_status_value_is_rejected(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.get("/api/v1/connected_account_transfers/tr_z").respond(200, json={"status": "WEIRD"})
    with pytest.raises(AirwallexAPIError, match="WEIRD"):
        client.get_status("tr_z")


def test_wait_for_settlement_polls_until_final(
    api: respx.MockRouter, client: RestAirwallexClient
) -> None:
    api.get("/api/v1/connected_account_transfers/tr_1").mock(
        side_effect=[
            httpx.Response(200, json={"status": "NEW"}),
            httpx.Response(200, json={"status": "SETTLED"}),
        ]
    )
    assert client.wait_for_settlement("tr_1") == "SETTLED"


def test_wait_for_settlement_times_out(
    api: respx.MockRouter, client: RestAirwallexClient, clock: Clock
) -> None:
    def still_new(_request: httpx.Request) -> httpx.Response:
        clock.now += 10
        return httpx.Response(200, json={"status": "NEW"})

    api.get("/api/v1/connected_account_transfers/tr_1").mock(side_effect=still_new)
    with pytest.raises(AirwallexTransientError, match="tr_1"):
        client.wait_for_settlement("tr_1", timeout_s=30)


def test_rejects_unknown_transfer_reason() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    with pytest.raises(ValueError, match="reason"):
        RestAirwallexClient(settings, transfer_reason="gift")


def test_login_network_error_is_transient(client: RestAirwallexClient) -> None:
    with respx.mock(base_url=BASE) as api:
        api.post("/api/v1/authentication/login").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(AirwallexTransientError, match="login"):
            client.get_balance(None)


def test_non_json_error_body(api: respx.MockRouter, client: RestAirwallexClient) -> None:
    api.get("/api/v1/balances/current").respond(403, text="<html>denied</html>")
    with pytest.raises(AirwallexAPIError) as info:
        client.get_balance(None)
    assert (info.value.status_code, info.value.code) == (403, "unknown")


def test_unparsable_retry_after_falls_back_to_backoff(clock: Clock) -> None:
    sleeps: list[float] = []
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        airwallex_client_id="cid",
        airwallex_api_key="key",
    )
    client = RestAirwallexClient(settings, clock=clock, sleep=sleeps.append)
    with respx.mock(base_url=BASE) as api:
        api.post("/api/v1/authentication/login").respond(201, json=TOKEN_BODY)
        api.get("/api/v1/balances/current").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "soon"}),
                httpx.Response(429),
                httpx.Response(200, json=[]),
            ]
        )
        client.get_balance(None)
    assert sleeps == [1.0, 2.0]
