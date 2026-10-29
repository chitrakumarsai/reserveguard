"""The demo scenario shared by tests, scripts/demo.py and the API."""

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from reserveguard.agent.evidence import EvidenceItem
from reserveguard.airwallex.client import AirwallexClient, AirwallexError
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.airwallex.sandbox import ConnectedAccountSpec, SandboxAdmin
from reserveguard.config import Settings
from reserveguard.models import Seller, Tier
from reserveguard.settlement import PaymentWaiter, SettlementService, stored_sellers
from reserveguard.store import Snapshot, SqliteStore

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SAMPLE_EVIDENCE_PATH = _DATA_DIR / "samples" / "evidence_seller_c.json"
# Written by scripts/sandbox_setup.py; git-ignored because the ids belong to one sandbox.
SANDBOX_SELLERS_PATH = _DATA_DIR / "sandbox_sellers.json"

# Account age cut-offs for the base tier when the seller file sets none.
NEW_SELLER_MAX_DAYS = 90
TRUSTED_SELLER_MIN_DAYS = 365
ACCOUNTS_PATH = "/api/v1/accounts"
MAX_ACCOUNT_PAGES = 50  # page_num starts at 0 (checked on the sandbox); a guard, not a limit
CACHED_SELLERS_WARNING = (
    "Using cached sellers: the Airwallex account list could not be loaded, so sellers come "
    "from the saved ledger or data/sandbox_sellers.json."
)

DEMO_SELLERS: tuple[Seller, ...] = (
    Seller("A", "Seller A", Tier.TRUSTED, "acct_seller_a"),
    Seller("B", "Seller B", Tier.STANDARD, "acct_seller_b"),
    Seller("C", "Seller C", Tier.NEW, "acct_seller_c"),
)
DEMO_GROSS_CENTS: dict[str, int] = {"A": 300_000, "B": 300_000, "C": 400_000}

DELIVERY_CONFIRMATIONS: tuple[EvidenceItem, ...] = tuple(
    EvidenceItem(
        id=f"ev-c-1{n:02d}",
        source="carrier",
        timestamp=datetime(2026, 10, 24, 9, n, tzinfo=UTC),
        text=f"Order ORD-C-{1000 + n}: delivered, signed for by recipient.",
    )
    for n in range(1, 5)
)


def build_demo_service() -> tuple[SettlementService, FakeAirwallexClient]:
    """Platform account pre-funded with the cycle's collected customer payments."""
    fake = FakeAirwallexClient({None: sum(DEMO_GROSS_CENTS.values())})
    return SettlementService(fake, DEMO_SELLERS), fake


def build_service(
    settings: Settings, sellers_path: Path = SANDBOX_SELLERS_PATH
) -> tuple[SettlementService, AirwallexClient, str | None]:
    """Fake client unless USE_FAKE_AIRWALLEX=false; then the REST client and the sellers
    Airwallex lists. The third value is a warning when cached sellers had to be used."""
    if settings.use_fake_airwallex:
        return (*build_demo_service(), None)
    seller_file = load_seller_file(sellers_path)
    client = RestAirwallexClient(settings)
    store = SqliteStore(settings.reserveguard_db_path)
    sellers, warning = load_live_sellers(client, seller_file, store.load(), datetime.now(UTC))
    service = SettlementService(client, sellers, await_payment=payment_waiter(client), store=store)
    return service, client, warning


def load_live_sellers(
    client: RestAirwallexClient, seller_file: "SellerFile", snapshot: Snapshot | None, now: datetime
) -> tuple[tuple[Seller, ...], str | None]:
    """Sellers from the Airwallex account list. If it cannot be loaded, the sellers of the
    saved ledger (or, with none, the seller file) and a warning, instead of not starting."""
    try:
        return sellers_from_accounts(fetch_accounts(client), seller_file, now), None
    except (AirwallexError, ValueError) as exc:
        logger.warning("Airwallex account list failed (%s); using cached sellers", exc)
    if snapshot is not None:
        return stored_sellers(snapshot, seller_file.tiers), CACHED_SELLERS_WARNING
    cached = tuple(
        Seller(sid, f"Seller {sid}", seller_file.tiers.get(sid, Tier.NEW), account_id)
        for sid, account_id in seller_file.accounts.items()
    )
    return cached, CACHED_SELLERS_WARNING


def fetch_accounts(client: RestAirwallexClient) -> list[Mapping[str, Any]]:
    """Every connected account, page by page. Pages that repeat an account are deduped."""
    found: dict[str, Mapping[str, Any]] = {}
    for page in range(MAX_ACCOUNT_PAGES):
        data = client.request("GET", ACCOUNTS_PATH, params={"page_num": str(page)})
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise ValueError("Airwallex account list has no 'items' list")
        found.update({str(i.get("id")): i for i in items})
        if not items or not data.get("has_more"):
            return list(found.values())
    raise ValueError(f"Airwallex account list still has_more after {MAX_ACCOUNT_PAGES} pages")


def tier_for_age(created_at: datetime, now: datetime) -> Tier:
    age = now - created_at
    if age < timedelta(days=NEW_SELLER_MAX_DAYS):
        return Tier.NEW
    if age < timedelta(days=TRUSTED_SELLER_MIN_DAYS):
        return Tier.STANDARD
    return Tier.TRUSTED


def sellers_from_accounts(
    accounts: Sequence[Mapping[str, Any]], seller_file: "SellerFile", now: datetime
) -> tuple[Seller, ...]:
    """ACTIVE, non-hidden accounts as sellers. Accounts in the seller file keep its seller id
    (so a saved ledger still matches) and its tier; the rest are tiered by account age."""
    alias = {account_id: sid for sid, account_id in seller_file.accounts.items()}
    sellers = []
    for item in accounts:
        account_id, status, created_at, name = _parse_account(item)
        if status != "ACTIVE" or account_id in seller_file.hidden:
            continue
        seller_id = alias.get(account_id, account_id)
        tier = seller_file.tiers.get(seller_id) or tier_for_age(created_at, now)
        sellers.append(Seller(seller_id, name, tier, account_id))
    missing = sorted(set(seller_file.accounts) - {s.seller_id for s in sellers})
    if missing:
        raise ValueError(f"Airwallex lists no ACTIVE account for sellers {', '.join(missing)}")
    return tuple(sorted(sellers, key=lambda s: s.seller_id))


def _parse_account(item: Mapping[str, Any]) -> tuple[str, str, datetime, str]:
    """(id, status, created_at, display name) of one listed account, or ValueError."""
    account_id, status = item.get("id"), item.get("status")
    if not (isinstance(account_id, str) and account_id.startswith("acct_")):
        raise ValueError(f"listed account has no valid id: {account_id!r}")
    if not isinstance(status, str):
        raise ValueError(f"listed account {account_id} has no status")
    try:
        created_at = datetime.fromisoformat(str(item.get("created_at")))
    except ValueError as exc:
        raise ValueError(f"listed account {account_id} has a bad created_at") from exc
    details = item.get("account_details") or {}
    business = details.get("business_details") or {} if isinstance(details, dict) else {}
    name = item.get("nickname") or business.get("business_name") or account_id
    return account_id, status, created_at, str(name)


def payment_waiter(client: AirwallexClient) -> PaymentWaiter:
    """Live: poll Airwallex until final or the time left runs out. Fake: final at once."""
    if isinstance(client, RestAirwallexClient):
        live = client
        return lambda object_id, left: live.wait_for_settlement(object_id, timeout_s=left)
    return lambda object_id, _left: client.get_status(object_id)


def demo_seller_specs(stamp: str) -> dict[str, ConnectedAccountSpec]:
    """One sandbox business per demo seller; ``stamp`` keeps emails unique across runs."""
    return {
        s.seller_id: ConnectedAccountSpec(
            nickname=f"ReserveGuard {s.name}",
            business_name="Sandbox Business",  # the sandbox auto-approves KYB for this name
            ein=f"12-345678{n}",
            email=f"reserveguard-seller-{s.seller_id.lower()}-{stamp}@example.com",
            address_line1="1 Market St",
            city="San Francisco",
            state="CA",
            postcode="94105",
        )
        for n, s in enumerate(DEMO_SELLERS, start=1)
    }


@dataclass(frozen=True)
class SellerFile:
    """data/sandbox_sellers.json: which sandbox account is demo seller A/B/C, optional tier
    overrides, and accounts to leave out (e.g. leftovers from an earlier onboarding)."""

    accounts: Mapping[str, str]  # seller_id -> account id
    tiers: Mapping[str, Tier] = field(default_factory=dict)  # seller_id -> override
    hidden: frozenset[str] = frozenset()


def save_seller_file(path: Path, seller_file: SellerFile) -> None:
    """Write via a temp file and rename, so a crash never leaves a half-written file."""
    sellers = {
        sid: {"account_id": account_id}
        | ({"tier": seller_file.tiers[sid].value} if sid in seller_file.tiers else {})
        for sid, account_id in seller_file.accounts.items()
    }
    body = {"sellers": sellers, "hidden": sorted(seller_file.hidden)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def onboard_demo_sellers(admin: SandboxAdmin, path: Path, stamp: str) -> dict[str, str]:
    """Onboard the demo sellers not yet in ``path``, saving after each one.

    A failure part-way keeps the accounts already created, and the next run resumes with
    only the missing sellers instead of orphaning sandbox accounts.
    """
    current = _parse_seller_file(json.loads(path.read_text()), path) if path.exists() else None
    seller_file = current or SellerFile(accounts={})
    demo_tiers = {s.seller_id: s.tier for s in DEMO_SELLERS}
    for seller_id, spec in demo_seller_specs(stamp).items():
        if seller_id not in seller_file.accounts:
            seller_file = replace(
                seller_file,
                accounts={**seller_file.accounts, seller_id: admin.onboard_seller(spec)},
                tiers={**seller_file.tiers, seller_id: demo_tiers[seller_id]},
            )
            save_seller_file(path, seller_file)
    return dict(seller_file.accounts)


def load_seller_accounts(path: Path) -> dict[str, str]:
    """seller_id -> connected account id, checked to cover every demo seller."""
    return dict(load_seller_file(path).accounts)


def load_seller_file(path: Path) -> SellerFile:
    """The seller file, checked to cover every demo seller."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run `uv run python scripts/sandbox_setup.py` first"
        )
    seller_file = _parse_seller_file(json.loads(path.read_text()), path)
    missing = [s.seller_id for s in DEMO_SELLERS if s.seller_id not in seller_file.accounts]
    if missing:
        raise ValueError(f"{path} has no account for sellers {', '.join(missing)}")
    return seller_file


def _parse_seller_file(raw: Any, path: Path) -> SellerFile:
    """Either {"sellers": {id: {"account_id", "tier"?}}, "hidden": [...]} or the older
    plain {id: account_id}."""
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object")
    if "sellers" not in raw:
        raw = {"sellers": {sid: {"account_id": acct} for sid, acct in raw.items()}}
    entries, hidden = raw["sellers"], raw.get("hidden", [])
    if not isinstance(entries, dict) or not all(isinstance(e, dict) for e in entries.values()):
        raise ValueError(f"{path}: 'sellers' must map seller ids to objects")
    accounts = {sid: e.get("account_id") for sid, e in entries.items()}
    if not isinstance(hidden, list):
        raise ValueError(f"{path}: 'hidden' must be a list of account ids")
    bad = [str(v) for v in [*accounts.values(), *hidden] if not _is_account_id(v)]
    if bad:
        raise ValueError(f"{path}: account ids must start with 'acct_' (got {', '.join(bad)})")
    if len(set(accounts.values())) != len(accounts):
        raise ValueError(f"{path}: an account is mapped to more than one seller")
    if set(hidden) & set(accounts.values()):
        raise ValueError(f"{path}: a seller's account is also hidden")
    try:
        tiers = {sid: Tier(e["tier"]) for sid, e in entries.items() if "tier" in e}
    except ValueError as exc:
        raise ValueError(f"{path}: unknown tier ({exc})") from exc
    return SellerFile(accounts=accounts, tiers=tiers, hidden=frozenset(hidden))


def _is_account_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("acct_")
