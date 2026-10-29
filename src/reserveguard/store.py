"""SQLite persistence for SettlementService, so a restart keeps every reserve and decision.

The ledger is stored as append-only rows (``ledger_entries``) so it can be audited with plain
SQL. The rest of the service state (rates, signals, pending decisions, memos, settled
cycles, payments still pending) is one JSON document written in the same transaction.
Uses the standard-library ``sqlite3``; one process at a time.
"""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reserveguard.models import EntryKind, LedgerEntry

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ledger_entries (
    seq INTEGER PRIMARY KEY,
    seller_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    reference TEXT NOT NULL,
    object_id TEXT
);
CREATE TABLE IF NOT EXISTS service_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    json TEXT NOT NULL
);
"""


class StoreError(Exception):
    """The stored ledger cannot be used safely with this configuration."""


@dataclass(frozen=True)
class Snapshot:
    opening_balance_cents: int
    accounts: Mapping[str, str]  # seller_id -> Airwallex account id
    entries: tuple[LedgerEntry, ...]
    state: Mapping[str, Any]  # JSON-safe service state


class SqliteStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._transaction() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            version = int(self._meta(conn)["schema_version"])
            self._rows = self._count(conn)
        if version != SCHEMA_VERSION:
            raise StoreError(f"{path} has schema version {version}, expected {SCHEMA_VERSION}")

    def load(self) -> Snapshot | None:
        """None for a new database; otherwise everything the last save() wrote."""
        with self._transaction() as conn:
            meta = self._meta(conn)
            if "opening_balance_cents" not in meta:
                return None
            rows = conn.execute(
                "SELECT seller_id, kind, amount_cents, reference, object_id"
                " FROM ledger_entries ORDER BY seq"
            ).fetchall()
            self._rows = len(rows)
            state_row = conn.execute("SELECT json FROM service_state WHERE id = 1").fetchone()
        entries = tuple(
            LedgerEntry(
                seller_id=seller_id,
                kind=EntryKind(kind),
                amount_cents=amount,
                reference=reference,
                object_id=object_id,
            )
            for seller_id, kind, amount, reference, object_id in rows
        )
        return Snapshot(
            opening_balance_cents=int(meta["opening_balance_cents"]),
            accounts=json.loads(meta["accounts"]),
            entries=entries,
            state=json.loads(state_row[0]) if state_row else {},
        )

    def save(self, snapshot: Snapshot) -> None:
        """Write the snapshot atomically. Ledger rows are only ever appended."""
        with self._transaction() as conn:
            stored = self._count(conn)
            if stored != self._rows:
                raise StoreError(
                    "refusing to save: the ledger changed on disk since it was loaded"
                    " (is another process using it?)"
                )
            if stored > len(snapshot.entries):
                raise StoreError("refusing to save: the ledger would lose entries")
            conn.executemany(
                "INSERT INTO ledger_entries"
                " (seller_id, kind, amount_cents, reference, object_id) VALUES (?, ?, ?, ?, ?)",
                [
                    (e.seller_id, e.kind.value, e.amount_cents, e.reference, e.object_id)
                    for e in snapshot.entries[stored:]
                ],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                [
                    ("opening_balance_cents", str(snapshot.opening_balance_cents)),
                    ("accounts", json.dumps(dict(snapshot.accounts), sort_keys=True)),
                ],
            )
            conn.execute(
                "INSERT OR REPLACE INTO service_state (id, json) VALUES (1, ?)",
                (json.dumps(snapshot.state, sort_keys=True),),
            )
        self._rows = len(snapshot.entries)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:  # commits on success, rolls back on any error
                yield conn

    @staticmethod
    def _count(conn: sqlite3.Connection) -> int:
        (rows,) = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()
        return int(rows)

    @staticmethod
    def _meta(conn: sqlite3.Connection) -> dict[str, str]:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
