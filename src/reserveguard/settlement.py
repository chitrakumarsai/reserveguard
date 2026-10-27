"""Orchestrates settlement cycles and reserve changes through an AirwallexClient."""

import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace

from pydantic import BaseModel, ConfigDict

from reserveguard.agent.tools import draft_memo
from reserveguard.airwallex.client import (
    AirwallexClient,
    AirwallexError,
    AirwallexTransientError,
)
from reserveguard.ledger import Ledger, SellerTotals, run_checks
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
    RateExplanation,
    base_rate_bps,
    explain_rate,
    plan_adjustment,
    propose_reserve_change,
    settle_gross,
)
from reserveguard.store import Snapshot, SqliteStore, StoreError

MAX_ATTEMPTS = 3
# One command (settle, assess, approve) waits at most this long for all its payments.
PAYMENT_WAIT_BUDGET_S = 60.0
_UNSETTLED_FINAL = frozenset({"FAILED", "CANCELLED"})

# (object_id, seconds left) -> status. Live: poll Airwallex; fake: read the status.
PaymentWaiter = Callable[[str, float], str]


class PaymentNotSettledError(AirwallexError):
    """A payment did not reach SETTLED. ``ledger_applied`` says whether the ledger already
    counts it (still PENDING: re-checked on replay) or not (FAILED/CANCELLED: retry redoes it)."""

    def __init__(self, payment_id: str, status: str, *, ledger_applied: bool) -> None:
        super().__init__(f"payment {payment_id} is {status}")
        self.payment_id = payment_id
        self.status = status
        self.ledger_applied = ledger_applied


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
class AdjustmentPreview:
    """What approve() would do now, before anything moves."""

    decision: ReserveDecision
    plan: AdjustmentPlan
    account_id: str
    wallet_cents: int
    memo: str


@dataclass(frozen=True)
class SellerStatement:
    """One seller's own view: its ledger lines only, plus its live wallet."""

    seller_id: str
    account_id: str
    rate_bps: int
    totals: SellerTotals
    entries: tuple[LedgerEntry, ...]
    wallet_cents: int


class _StoredSeller(BaseModel):
    rate_bps: int
    gross_cents: int
    signals: tuple[RiskSignal, ...]
    pending: ReserveDecision | None
    memos: tuple[str, ...]


class _StoredState(BaseModel):
    """Everything besides the ledger rows that must survive a restart."""

    sellers: dict[str, _StoredSeller]
    settled: list[tuple[str, str, SettlementLine]]
    unconfirmed: list[str]


@dataclass(frozen=True)
class _SellerState:
    seller: Seller
    rate_bps: int
    gross_cents: int = 0
    signals: tuple[RiskSignal, ...] = ()
    pending: ReserveDecision | None = None
    memos: tuple[str, ...] = ()


class SettlementService:
    def __init__(
        self,
        client: AirwallexClient,
        sellers: Sequence[Seller],
        *,
        await_payment: PaymentWaiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        store: SqliteStore | None = None,
    ) -> None:
        """With a ``store``, state is loaded from it and saved after every command."""
        _validate_unique(sellers)
        self._client = client
        self._await = await_payment or (lambda object_id, _left: client.get_status(object_id))
        self._clock = clock
        self._deadline = 0.0
        self._store = store
        self._unconfirmed: set[str] = set()
        self._states = {s.seller_id: _SellerState(s, base_rate_bps(s.tier)) for s in sellers}
        self._ledger = Ledger()
        self._settled: dict[tuple[str, str], SettlementLine] = {}
        self._lock = threading.RLock()
        self.last_settlement: tuple[SettlementLine, ...] = ()
        snapshot = store.load() if store else None
        if snapshot is None:
            # The platform account may hold money this ledger never touched (e.g. sandbox
            # float), so reconciliation compares only what moved after this point.
            self._opening_balance_cents = client.get_balance(None)
            self._persist()
        else:
            self._restore(snapshot)

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

    def explain(self, seller_id: str) -> RateExplanation:
        """Why the evidence on file gives this rate (the pending one, if awaiting approval)."""
        with self._lock:
            state = self._state(seller_id)
            return explain_rate(state.seller.tier, state.signals)

    def pending(self, seller_id: str) -> ReserveDecision | None:
        return self._state(seller_id).pending

    def preview(self, seller_id: str) -> AdjustmentPreview:
        """Plan the pending decision against the seller's live balance. Moves no money."""
        with self._lock:
            state = self._state(seller_id)
            if state.pending is None:
                raise LookupError(f"no pending reserve decision for seller {seller_id}")
            plan, wallet = self._plan(state.pending)
            return AdjustmentPreview(
                decision=state.pending,
                plan=plan,
                account_id=state.seller.account_id,
                wallet_cents=wallet,
                memo=draft_memo(state.pending),
            )

    def statement(self, seller_id: str) -> SellerStatement:
        with self._lock:
            state = self._state(seller_id)
            seller = state.seller
            return SellerStatement(
                seller_id=seller_id,
                account_id=seller.account_id,
                rate_bps=state.rate_bps,
                totals=self._ledger.totals(seller_id),
                entries=tuple(e for e in self._ledger.entries if e.seller_id == seller_id),
                wallet_cents=self._client.get_balance(seller.account_id, seller.currency),
            )

    def reconciliation(self) -> list[CheckResult]:
        """The platform holds this cycle's gross as collected, then pays out of it, so its
        ledger-attributable balance is opening movement plus gross collected."""
        with self._lock:
            moved = self._client.get_balance(None) - self._opening_balance_cents
            gross = sum(self._ledger.totals(s).gross_cents for s in self._ledger.seller_ids())
            return run_checks(self._ledger, moved + gross)

    # --- commands ------------------------------------------------------------
    def settle_cycle(
        self, gross_by_seller: Mapping[str, int], *, cycle_id: str
    ) -> list[SettlementLine]:
        """Settle one cycle. Replaying the same cycle_id never pays a seller twice."""
        with self._command():
            states = [self._state(seller_id) for seller_id in gross_by_seller]  # validate first
            lines = []
            for state in states:
                seller_id = state.seller.seller_id
                done = self._settled.get((cycle_id, seller_id))
                if done is None:
                    done = self._settle_one(state, gross_by_seller[seller_id], cycle_id)
                    self._settled[(cycle_id, seller_id)] = done
                    self._raise_if_pending(done.transfer_id)
                else:
                    self._recheck(done.transfer_id)
                lines.append(done)
            self.last_settlement = tuple(lines)
            return lines

    def assess(self, seller_id: str, new_signals: Sequence[RiskSignal]) -> ReserveDecision:
        """Recalculate ONE seller's reserve. Large increases wait for approve().

        Signals already on file are ignored, so re-sending the same evidence is a no-op.
        """
        return self.assess_detailed(seller_id, new_signals)[0]

    def assess_detailed(
        self, seller_id: str, new_signals: Sequence[RiskSignal], *, hold_releases: bool = False
    ) -> tuple[ReserveDecision, str | None]:
        """assess(), plus the memo when the change was applied at once (None while pending).
        ``hold_releases`` sends any decrease to approve() as well."""
        with self._command():
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
            releases = decision.target_reserve_cents < decision.held_cents
            if hold_releases and releases and not decision.needs_approval:
                decision = decision.model_copy(update={"needs_approval": True})
            if decision.needs_approval:
                self._states[seller_id] = replace(state, signals=signals, pending=decision)
                return decision, None
            return decision, self._apply(decision, signals).memo

    def approve(self, seller_id: str) -> AdjustmentResult:
        with self._command():
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
        # The amount is part of the key: the same cycle with a different amount is a new payout.
        key = str(
            uuid.uuid5(_KEY_NAMESPACE, f"payout:{cycle_id}:{seller.seller_id}:{b.payout_cents}")
        )
        transfer_id = None
        if b.payout_cents > 0:
            transfer_id = self._with_retry(
                lambda k: self._client.transfer_to_seller(
                    seller.account_id, b.payout_cents, seller.currency, ref, k
                ),
                key,
            )
            self._confirm(transfer_id, recorded_before=False)
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
        plan, _ = self._plan(decision)
        ref = f"reserve:{seller.seller_id}"
        charge_id = release_id = None
        if plan.charge_cents > 0:
            charge_id = self._with_retry(
                lambda key: self._client.charge_seller(
                    seller.account_id, plan.charge_cents, seller.currency, ref, key
                )
            )
            self._confirm(charge_id, recorded_before=False)
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
            self._confirm(release_id, recorded_before=plan.charge_cents > 0)
            self._record(seller.seller_id, EntryKind.RELEASE, plan.release_cents, ref, release_id)
        memo = draft_memo(decision)
        self._states[seller.seller_id] = replace(
            self._state(seller.seller_id),
            rate_bps=decision.new_rate_bps,
            signals=signals,
            pending=None,
            memos=(*state.memos, memo),
        )
        for object_id in (charge_id, release_id):
            self._raise_if_pending(object_id)
        return AdjustmentResult(decision=decision, plan=plan, memo=memo)

    def _plan(self, decision: ReserveDecision) -> tuple[AdjustmentPlan, int]:
        """The adjustment for ``decision`` and the seller wallet it was sized against."""
        seller = self._state(decision.seller_id).seller
        totals = self._ledger.totals(seller.seller_id)
        # Only THIS seller's balance is read; no other tenant's funds are considered.
        balance = self._client.get_balance(seller.account_id, seller.currency)
        plan = plan_adjustment(
            target_cents=decision.target_reserve_cents,
            held_cents=totals.held_cents,
            seller_balance_cents=balance
            if decision.target_reserve_cents > totals.held_cents
            else 0,
            existing_debt_cents=totals.debt_cents,
        )
        return plan, balance

    @contextmanager
    def _command(self) -> Iterator[None]:
        """One money command: under the lock, with one wait deadline, saved on the way out
        even when it fails part-way (a payment recorded as pending must not be forgotten)."""
        with self._lock:
            self._start_deadline()
            try:
                yield
            finally:
                self._persist()

    def _persist(self) -> None:
        if self._store is None:
            return
        state = _StoredState(
            sellers={
                seller_id: _StoredSeller(
                    rate_bps=s.rate_bps,
                    gross_cents=s.gross_cents,
                    signals=s.signals,
                    pending=s.pending,
                    memos=s.memos,
                )
                for seller_id, s in self._states.items()
            },
            settled=[(cycle, seller, line) for (cycle, seller), line in self._settled.items()],
            unconfirmed=sorted(self._unconfirmed),
        )
        self._store.save(
            Snapshot(
                opening_balance_cents=self._opening_balance_cents,
                accounts={sid: s.seller.account_id for sid, s in self._states.items()},
                entries=self._ledger.entries,
                state=state.model_dump(mode="json"),
            )
        )

    def _restore(self, snapshot: Snapshot) -> None:
        accounts = {sid: s.seller.account_id for sid, s in self._states.items()}
        if dict(snapshot.accounts) != accounts:
            raise StoreError(
                "seller accounts differ from the stored ledger; refusing to mix sellers' money"
            )
        state = _StoredState.model_validate(snapshot.state)
        self._opening_balance_cents = snapshot.opening_balance_cents
        self._ledger = Ledger(entries=snapshot.entries)
        self._settled = {(cycle, seller): line for cycle, seller, line in state.settled}
        self._unconfirmed = set(state.unconfirmed)
        for seller_id, stored in state.sellers.items():
            self._states[seller_id] = replace(
                self._states[seller_id],
                rate_bps=stored.rate_bps,
                gross_cents=stored.gross_cents,
                signals=stored.signals,
                pending=stored.pending,
                memos=stored.memos,
            )

    def _start_deadline(self) -> None:
        self._deadline = self._clock() + PAYMENT_WAIT_BUDGET_S

    def _wait(self, object_id: str) -> str:
        """Wait within this command's deadline; a wait that times out counts as PENDING."""
        left = max(0.0, self._deadline - self._clock())
        try:
            return self._await(object_id, left)
        except AirwallexTransientError:
            return "PENDING"

    def _confirm(self, object_id: str, *, recorded_before: bool) -> None:
        """Called right after a payment is created and BEFORE it is recorded: a failed
        payment is never recorded; a still-pending one is recorded and re-checked later."""
        status = self._wait(object_id)
        if status in _UNSETTLED_FINAL:
            raise PaymentNotSettledError(object_id, status, ledger_applied=recorded_before)
        if status != "SETTLED":
            self._unconfirmed.add(object_id)

    def _raise_if_pending(self, object_id: str | None) -> None:
        if object_id is not None and object_id in self._unconfirmed:
            raise PaymentNotSettledError(object_id, "PENDING", ledger_applied=True)

    def _recheck(self, object_id: str | None) -> None:
        """On replay, wait again for a recorded payment that was still pending."""
        if object_id is None or object_id not in self._unconfirmed:
            return
        status = self._wait(object_id)
        if status != "SETTLED":
            raise PaymentNotSettledError(object_id, status, ledger_applied=True)
        self._unconfirmed.discard(object_id)

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
