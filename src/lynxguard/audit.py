"""Append-only, hash-chained audit log.

Each organization has its own chain. Every record stores the hash of the record
before it, so editing or deleting any past record breaks verification. Records
are never updated: a reviewer's decision is a NEW record that references the check.

SQLite is used for v1. The interface is small so it can move to Postgres later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id      TEXT NOT NULL,
    record_id   TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,            -- 'check' | 'decision'
    ref_id      TEXT,                     -- for decisions: the check record_id
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL,            -- canonical JSON
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log (org_id, seq);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, kind: str, ref_id: str | None, created_at: str, payload: str) -> str:
    material = "|".join([prev_hash, kind, ref_id or "", created_at, payload])
    return hashlib.sha256(material.encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def _last_hash(self, org_id: str) -> str:
        row = self._db.execute(
            "SELECT hash FROM audit_log WHERE org_id=? ORDER BY seq DESC LIMIT 1", (org_id,)
        ).fetchone()
        return row["hash"] if row else GENESIS

    def append(self, org_id: str, kind: str, payload: dict, ref_id: str | None = None) -> dict:
        with self._lock:
            record_id = str(uuid.uuid4())
            created_at = datetime.now(timezone.utc).isoformat()
            body = canonical(payload)
            prev = self._last_hash(org_id)
            h = _hash(prev, kind, ref_id, created_at, body)
            self._db.execute(
                "INSERT INTO audit_log (org_id, record_id, kind, ref_id, created_at, payload, prev_hash, hash)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (org_id, record_id, kind, ref_id, created_at, body, prev, h),
            )
            self._db.commit()
            return {"record_id": record_id, "created_at": created_at, "hash": h, "prev_hash": prev}

    def get(self, org_id: str, record_id: str) -> dict | None:
        row = self._db.execute(
            "SELECT * FROM audit_log WHERE org_id=? AND record_id=?", (org_id, record_id)
        ).fetchone()
        return self._row(row) if row else None

    def list(self, org_id: str, limit: int = 100) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM audit_log WHERE org_id=? ORDER BY seq DESC LIMIT ?", (org_id, limit)
        ).fetchall()
        return [self._row(r) for r in rows]

    def decisions_for(self, org_id: str, record_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT * FROM audit_log WHERE org_id=? AND kind='decision' AND ref_id=? ORDER BY seq",
            (org_id, record_id),
        ).fetchall()
        return [self._row(r) for r in rows]

    def verify(self, org_id: str) -> dict:
        prev = GENESIS
        count = 0
        for row in self._db.execute(
            "SELECT * FROM audit_log WHERE org_id=? ORDER BY seq", (org_id,)
        ):
            expected = _hash(prev, row["kind"], row["ref_id"], row["created_at"], row["payload"])
            if row["prev_hash"] != prev or row["hash"] != expected:
                return {"valid": False, "records_checked": count, "broken_at": row["record_id"]}
            prev = row["hash"]
            count += 1
        return {"valid": True, "records_checked": count, "head": prev}

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        return {
            "record_id": row["record_id"], "kind": row["kind"], "ref_id": row["ref_id"],
            "created_at": row["created_at"], "payload": json.loads(row["payload"]),
            "hash": row["hash"], "prev_hash": row["prev_hash"],
        }
