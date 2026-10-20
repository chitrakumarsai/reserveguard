"""The demo scenario shared by tests, scripts/demo.py and the API."""

from datetime import UTC, datetime
from pathlib import Path

from reserveguard.agent.evidence import EvidenceItem
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.models import Seller, Tier
from reserveguard.settlement import SettlementService

SAMPLE_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "samples" / "evidence_seller_c.json"
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
