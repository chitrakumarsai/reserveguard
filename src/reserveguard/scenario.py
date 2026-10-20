"""The demo scenario shared by tests, scripts/demo.py and the API."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from reserveguard.agent.evidence import EvidenceItem
from reserveguard.airwallex.client import AirwallexClient
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.airwallex.rest import RestAirwallexClient
from reserveguard.airwallex.sandbox import ConnectedAccountSpec, SandboxAdmin
from reserveguard.config import Settings
from reserveguard.models import Seller, Tier
from reserveguard.settlement import SettlementService

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SAMPLE_EVIDENCE_PATH = _DATA_DIR / "samples" / "evidence_seller_c.json"
# Written by scripts/sandbox_setup.py; git-ignored because the ids belong to one sandbox.
SANDBOX_SELLERS_PATH = _DATA_DIR / "sandbox_sellers.json"

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
) -> tuple[SettlementService, AirwallexClient]:
    """Fake client unless USE_FAKE_AIRWALLEX=false; then the REST client and real sellers."""
    if settings.use_fake_airwallex:
        return build_demo_service()
    accounts = load_seller_accounts(sellers_path)
    sellers = tuple(replace(s, account_id=accounts[s.seller_id]) for s in DEMO_SELLERS)
    client = RestAirwallexClient(settings)
    return SettlementService(client, sellers), client


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


def save_seller_accounts(path: Path, accounts: dict[str, str]) -> None:
    """Write via a temp file and rename, so a crash never leaves a half-written file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(accounts, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def onboard_demo_sellers(admin: SandboxAdmin, path: Path, stamp: str) -> dict[str, str]:
    """Onboard the demo sellers not yet in ``path``, saving after each one.

    A failure part-way keeps the accounts already created, and the next run resumes with
    only the missing sellers instead of orphaning sandbox accounts.
    """
    accounts: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    for seller_id, spec in demo_seller_specs(stamp).items():
        if seller_id not in accounts:
            accounts = {**accounts, seller_id: admin.onboard_seller(spec)}
            save_seller_accounts(path, accounts)
    return accounts


def load_seller_accounts(path: Path) -> dict[str, str]:
    """seller_id -> connected account id, checked to cover every demo seller."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run `uv run python scripts/sandbox_setup.py` first"
        )
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must map seller ids to account ids")
    missing = [s.seller_id for s in DEMO_SELLERS if s.seller_id not in raw]
    if missing:
        raise ValueError(f"{path} has no account for sellers {', '.join(missing)}")
    accounts = {s.seller_id: raw[s.seller_id] for s in DEMO_SELLERS}
    bad = [k for k, v in accounts.items() if not (isinstance(v, str) and v.startswith("acct_"))]
    if bad:
        raise ValueError(f"{path}: account ids must start with 'acct_' (sellers {', '.join(bad)})")
    return accounts
