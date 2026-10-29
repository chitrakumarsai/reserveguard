"""Domain types. All money is integer cents; all rates are integer basis points."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Tier(StrEnum):
    TRUSTED = "trusted"
    STANDARD = "standard"
    NEW = "new"


class RiskKind(StrEnum):
    COMPLAINT = "complaint"
    COMPLAINT_CLUSTER = "complaint_cluster"
    TRACKING_FAILURE = "tracking_failure"
    DELIVERY_CONFIRMED = "delivery_confirmed"


class EntryKind(StrEnum):
    GROSS = "gross"
    FEE = "fee"
    RESERVE_HOLD = "reserve_hold"
    PAYOUT = "payout"
    CHARGE = "charge"
    REFUND = "refund"
    RELEASE = "release"
    DEBT = "debt"
    DEBT_CANCEL = "debt_cancel"
    DEBT_COLLECTED = "debt_collected"


@dataclass(frozen=True)
class Seller:
    seller_id: str
    name: str
    tier: Tier
    account_id: str
    currency: str = "USD"


class RiskSignal(BaseModel):
    """A risk observation produced from evidence. Never carries a money amount."""

    model_config = ConfigDict(frozen=True)

    kind: RiskKind
    weight_bps: int = Field(ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    clears: RiskKind | None = None
    summary: str = ""


class LedgerEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    seller_id: str
    kind: EntryKind
    amount_cents: int = Field(ge=0)
    reference: str
    object_id: str | None = None


class ReserveDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    seller_id: str
    old_rate_bps: int
    new_rate_bps: int
    old_target_cents: int
    target_reserve_cents: int
    held_cents: int
    needs_approval: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str
