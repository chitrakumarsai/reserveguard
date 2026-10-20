"""Orchestrates settlement cycles and reserve changes through an AirwallexClient."""

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from pydantic import BaseModel, ConfigDict

from reserveguard.agent.tools import draft_memo
from reserveguard.airwallex.client import AirwallexClient, AirwallexTransientError
from reserveguard.ledger import Ledger, run_checks
from reserveguard.models import (
    CheckResult,
    EntryKind,
    LedgerEntry,
    ReserveDecision,
    RiskSignal,
    Seller,
)
from reserveguard.reserve_engine import (
    AdjustmentPlan,
    base_rate_bps,
    plan_adjustment,
    propose_reserve_change,
    settle_gross,
)

MAX_ATTEMPTS = 3
_KEY_NAMESPACE = uuid.UUID("5b0c1f9e-6f0a-4c1e-9d4c-7a2f1e3b8c01")


class SettlementLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    seller_id: str
    gross_cents: int
    fee_cents: int
    rate_bps: int
    reserve_cents: int
    payout_cents: int
    transfer_id: str | None


@dataclass(frozen=True)
class AdjustmentResult:
    decision: ReserveDecision
    plan: AdjustmentPlan
    memo: str


@dataclass(frozen=True)
class _SellerState:
    seller: Seller
    rate_bps: int
    gross_cents: int = 0
    signals: tuple[RiskSignal, ...] = ()
    pending: ReserveDecision | None = None
    memos: tuple[str, ...] = ()


class SettlementService:
    def __init__(self, client: AirwallexClient, sellers: Sequence[Seller]) -> None:
        _validate_unique(sellers)
        self._client = client
        self._states = {s.seller_id: _SellerState(s, base_rate_bps(s.tier)) for s in sellers}
        self._ledger = Ledger()
        self._settled: dict[tuple[str, str], SettlementLine] = {}
        self._lock = threading.RLock()
        self.last_settlement: tuple[SettlementLine, ...] = ()

    # --- read views ----------------------------------------------------------
    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def sellers(self) -> tuple[Seller, ...]:
        return tuple(s.seller for s in self._states.values())

    def current_rate_bps(self, seller_id: str) -> int:
        return self._state(seller_id).rate_bps

    def memos(self, seller_id: str) -> tuple[str, ...]:
        return self._state(seller_id).memos

    def reconciliation(self) -> list[CheckResult]:
        with self._lock:
            return run_checks(self._ledger, self._client.get_balance(None))

    # --- commands ------------------------------------------------------------
    def settle_cycle(
        self, gross_by_seller: Mapping[str, int], *, cycle_id: str
    ) -> list[SettlementLine]:
        """Settle one cycle. Replaying the same cycle_id never pays a seller twice."""
        with self._lock:
            states = [self._state(seller_id) for seller_id in gross_by_seller]  # validate first
            lines = []
            for state in states:
                seller_id = state.seller.seller_id
                done = self._settled.get((cycle_id, seller_id))
                if done is None:
                    done = self._settle_one(state, gross_by_seller[seller_id], cycle_id)
                    self._settled[(cycle_id, seller_id)] = done
                lines.append(done)
            self.last_settlement = tuple(lines)
            return lines

    def assess(self, seller_id: str, new_signals: Sequence[RiskSignal]) -> ReserveDecision:
        """Recalculate ONE seller's reserve. Large increases wait for approve().

        Signals already on file are ignored, so re-sending the same evidence is a no-op.
        """
        with self._lock:
            state = self._state(seller_id)
            fresh = tuple(s for s in dict.fromkeys(new_signals) if s not in state.signals)
            signals = (*state.signals, *fresh)
            decision = propose_reserve_change(
                seller_id=seller_id,
                gross_cents=state.gross_cents,
                tier=state.seller.tier,
                current_rate_bps=state.rate_bps,
                held_cents=self._ledger.totals(seller_id).held_cents,
                signals=signals,
            )
            if decision.needs_approval:
                self._states[seller_id] = replace(state, signals=signals, pending=decision)
            else:
                self._apply(decision, signals)
            return decision

    def approve(self, seller_id: str) -> AdjustmentResult:
        with self._lock:
            state = self._state(seller_id)
            if state.pending is None:
                raise LookupError(f"no pending reserve decision for seller {seller_id}")
            return self._apply(state.pending, state.signals)

    # --- internals -----------------------------------------------------------
    def _state(self, seller_id: str) -> _SellerState:
        if seller_id not in self._states:
            raise KeyError(f"unknown seller {seller_id}")
        return self._states[seller_id]

    def _record(
        self, seller_id: str, kind: EntryKind, cents: int, ref: str, obj: str | None
    ) -> None:
        entry = LedgerEntry(
            seller_id=seller_id, kind=kind, amount_cents=cents, reference=ref, object_id=obj
        )
        self._ledger = self._ledger.record(entry)

    def _settle_one(self, state: _SellerState, gross_cents: int, cycle_id: str) -> SettlementLine:
        seller = state.seller
        b = settle_gross(gross_cents, state.rate_bps)
        ref = f"settle:{cycle_id}:{seller.seller_id}"
        # Deterministic key: a replay of this cycle after a crash reuses it, so Airwallex dedupes.
        key = str(uuid.uuid5(_KEY_NAMESPACE, f"payout:{cycle_id}:{seller.seller_id}"))
        transfer_id = None
        if b.payout_cents > 0:
            transfer_id = self._with_retry(
                lambda k: self._client.transfer_to_seller(
                    seller.account_id, b.payout_cents, seller.currency, ref, k
                ),
                key,
            )
        for kind, cents in [
            (EntryKind.GROSS, b.gross_cents),
            (EntryKind.FEE, b.fee_cents),
            (EntryKind.RESERVE_HOLD, b.reserve_cents),
        ]:
            self._record(seller.seller_id, kind, cents, ref, None)
        self._record(seller.seller_id, EntryKind.PAYOUT, b.payout_cents, ref, transfer_id)
        # A pending decision was sized on the old gross; drop it so the next assess re-proposes.
        self._states[seller.seller_id] = replace(
            state, gross_cents=state.gross_cents + gross_cents, pending=None
        )
        return SettlementLine(
            seller_id=seller.seller_id,
            gross_cents=b.gross_cents,
            fee_cents=b.fee_cents,
            rate_bps=b.rate_bps,
            reserve_cents=b.reserve_cents,
            payout_cents=b.payout_cents,
            transfer_id=transfer_id,
        )

    def _apply(
        self, decision: ReserveDecision, signals: tuple[RiskSignal, ...]
    ) -> AdjustmentResult:
        state = self._state(decision.seller_id)
        seller = state.seller
        totals = self._ledger.totals(seller.seller_id)
        # Only THIS seller's balance is read; no other tenant's funds are considered.
        balance = (
            self._client.get_balance(seller.account_id, seller.currency)
            if decision.target_reserve_cents > totals.held_cents
            else 0
        )
        plan = plan_adjustment(
            target_cents=decision.target_reserve_cents,
            held_cents=totals.held_cents,
            seller_balance_cents=balance,
            existing_debt_cents=totals.debt_cents,
        )
        ref = f"reserve:{seller.seller_id}"
        if plan.charge_cents > 0:
            charge_id = self._with_retry(
                lambda key: self._client.charge_seller(
                    seller.account_id, plan.charge_cents, seller.currency, ref, key
                )
            )
            self._record(seller.seller_id, EntryKind.CHARGE, plan.charge_cents, ref, charge_id)
        debt_delta = plan.debt_cents - totals.debt_cents
        if debt_delta > 0:
            self._record(seller.seller_id, EntryKind.DEBT, debt_delta, ref, None)
        elif debt_delta < 0:
            self._record(seller.seller_id, EntryKind.DEBT_CANCEL, -debt_delta, ref, None)
        if plan.release_cents > 0:
            release_id = self._with_retry(
                lambda key: self._client.transfer_to_seller(
                    seller.account_id, plan.release_cents, seller.currency, ref, key
                )
            )
            self._record(seller.seller_id, EntryKind.RELEASE, plan.release_cents, ref, release_id)
        memo = draft_memo(decision)
        self._states[seller.seller_id] = replace(
            self._state(seller.seller_id),
            rate_bps=decision.new_rate_bps,
            signals=signals,
            pending=None,
            memos=(*state.memos, memo),
        )
        return AdjustmentResult(decision=decision, plan=plan, memo=memo)

    @staticmethod
    def _with_retry(call: Callable[[str], str], key: str | None = None) -> str:
        """One idempotency key per logical operation, reused on every retry."""
        key = key or str(uuid.uuid4())
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return call(key)
            except AirwallexTransientError:
                if attempt == MAX_ATTEMPTS:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover


def _validate_unique(sellers: Sequence[Seller]) -> None:
    """Two sellers sharing an id or account would let one tenant's money touch another's."""
    for attr in ("seller_id", "account_id"):
        values = [getattr(s, attr) for s in sellers]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate {attr} among sellers")
