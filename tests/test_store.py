"""The SQLite store: every command's effect survives a restart, and a restart never
double-pays, loses a pending approval, or mixes up which account belongs to which seller."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.config import Settings
from reserveguard.models import Seller, Tier
from reserveguard.scenario import DEMO_GROSS_CENTS, DEMO_SELLERS, SAMPLE_EVIDENCE_PATH
from reserveguard.settlement import PaymentNotSettledError, SettlementService, stored_sellers
from reserveguard.store import SCHEMA_VERSION, SqliteStore, StoreError

pytestmark = pytest.mark.unit

RISK = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), Settings(use_llm=False))


def _fake() -> FakeAirwallexClient:
    return FakeAirwallexClient({None: sum(DEMO_GROSS_CENTS.values())})


def _service(fake: FakeAirwallexClient, db: Path, **kwargs: object) -> SettlementService:
    return SettlementService(fake, DEMO_SELLERS, store=SqliteStore(db), **kwargs)  # type: ignore[arg-type]


def _snapshot(service: SettlementService) -> tuple[object, ...]:
    return (
        service.ledger.entries,
        {s.seller_id: service.current_rate_bps(s.seller_id) for s in service.sellers},
        service.pending("C"),
        service.memos("C"),
        service.explain("C"),
    )


def test_every_command_survives_a_restart(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    before = _service(fake, db)
    before.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    before.assess("C", RISK)  # large increase: pending approval

    after = _service(fake, db)

    assert _snapshot(after) == _snapshot(before)
    assert all(c.passed for c in after.reconciliation())  # opening balance was kept
    assert after.approve("C").plan.charge_cents == 80_000  # the pending decision survived
    assert all(c.passed for c in _service(fake, db).reconciliation())


def test_a_second_process_cannot_overwrite_the_ledger(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    first, second = _service(fake, db), _service(fake, db)
    first.settle_cycle({"A": 300_000}, cycle_id="c1")

    with pytest.raises(StoreError, match="another process"):
        second.settle_cycle({"B": 300_000}, cycle_id="c2")

    assert _service(fake, db).ledger.entries == first.ledger.entries


def test_a_failed_payment_key_is_not_reused_after_a_restart(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    statuses = iter(["FAILED"])
    failing = _service(fake, db, await_payment=lambda _id, _left: next(statuses, "SETTLED"))
    with pytest.raises(PaymentNotSettledError):
        failing.settle_cycle({"A": 300_000}, cycle_id="c1")

    _service(fake, db).settle_cycle({"A": 300_000}, cycle_id="c1")

    assert len(set(fake.calls)) == 2


def test_replaying_a_cycle_after_a_restart_never_pays_twice(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    first = _service(fake, db).settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")

    again = _service(fake, db).settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")

    assert again == first
    assert fake.get_balance("acct_seller_a") == 255_000


def test_payment_pending_at_restart_is_rechecked(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    pending = _service(fake, db, await_payment=lambda _id, _left: "PENDING")
    with pytest.raises(PaymentNotSettledError) as first:
        pending.settle_cycle({"A": 300_000}, cycle_id="c1")
    assert first.value.ledger_applied is True  # recorded before the error, and saved

    checked: list[str] = []

    def settled(object_id: str, _left: float) -> str:
        checked.append(object_id)
        return "SETTLED"

    lines = _service(fake, db, await_payment=settled).settle_cycle({"A": 300_000}, cycle_id="c1")

    assert checked == [first.value.payment_id]
    assert lines[0].transfer_id == first.value.payment_id
    assert fake.get_balance("acct_seller_a") == 255_000  # paid once


def test_a_changed_seller_account_is_refused(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    _service(fake, db).settle_cycle({"A": 100_000}, cycle_id="c1")
    moved = tuple(
        replace(s, account_id="acct_someone_else") if s.seller_id == "A" else s
        for s in DEMO_SELLERS
    )

    with pytest.raises(StoreError, match="account"):
        SettlementService(fake, moved, store=SqliteStore(db))


def test_a_seller_missing_since_the_last_save_is_refused(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    _service(fake, db).settle_cycle({"A": 100_000}, cycle_id="c1")

    with pytest.raises(StoreError, match="A"):
        SettlementService(fake, DEMO_SELLERS[1:], store=SqliteStore(db))


def test_a_seller_added_since_the_last_save_starts_fresh(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    _service(fake, db).settle_cycle({"A": 100_000}, cycle_id="c1")
    added = Seller("D", "Seller D", Tier.NEW, "acct_seller_d")

    service = SettlementService(fake, (*DEMO_SELLERS, added), store=SqliteStore(db))

    assert service.ledger.totals("A").gross_cents == 100_000
    assert service.current_rate_bps("D") == 1_500


def test_saved_ledger_remembers_each_sellers_name_and_tier(tmp_path: Path) -> None:
    db = tmp_path / "rg.db"
    _service(_fake(), db)
    snapshot = SqliteStore(db).load()
    assert snapshot is not None
    assert stored_sellers(snapshot, {}) == DEMO_SELLERS


def test_ledger_is_kept_as_rows(tmp_path: Path) -> None:
    fake, db = _fake(), tmp_path / "rg.db"
    service = _service(fake, db)
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT seller_id, kind, amount_cents, object_id FROM ledger_entries ORDER BY seq"
        ).fetchall()

    assert len(rows) == len(service.ledger.entries)
    assert rows[3] == ("A", "payout", 255_000, service.ledger.entries[3].object_id)


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "rg.db"
    _service(_fake(), db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION + 1,)
        )

    with pytest.raises(StoreError, match="schema"):
        _service(_fake(), db)


def test_payout_key_depends_on_the_amount() -> None:
    keys: list[str] = []

    class SpyClient(FakeAirwallexClient):
        def transfer_to_seller(self, *args: object) -> str:
            keys.append(str(args[-1]))
            return super().transfer_to_seller(*args)  # type: ignore[arg-type]

    for gross in (300_000, 200_000):
        spy = SpyClient({None: 1_000_000})
        SettlementService(spy, DEMO_SELLERS).settle_cycle({"A": gross}, cycle_id="c1")

    assert keys[0] != keys[1]  # same cycle and seller, different amount: a new operation


def test_store_refuses_to_drop_ledger_rows(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "rg.db")
    service = SettlementService(_fake(), DEMO_SELLERS, store=store)
    service.settle_cycle({"A": 100_000}, cycle_id="c1")
    snapshot = store.load()
    assert snapshot is not None

    with pytest.raises(StoreError, match="lose entries"):
        store.save(replace(snapshot, entries=snapshot.entries[:-1]))
