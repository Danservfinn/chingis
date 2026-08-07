"""Append-only audit log with a per-task hash chain. Spec §2.

Every event, decision, and worker artifact is appended BEFORE its effects apply.
That ordering is what makes replay meaningful: the log is not a record of what happened,
it is the thing that happened.

The chain is per task_id. Tampering with event N invalidates every hash from N onward,
so `verify_chain` is a real integrity check rather than a checksum of the last row.
"""

from __future__ import annotations

import hashlib
import sqlite3

from .events import Event, EventType, canonical_json

GENESIS = "0" * 64


def _link(prev_hash: str, body: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---------------------------------------------------------------- writing --
    def head(self, task_id: str) -> tuple[int, str]:
        """(last_seq, last_hash) for a task. (-1, GENESIS) if the task is new."""
        row = self.conn.execute(
            "SELECT seq, audit_hash FROM events WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return (row["seq"], row["audit_hash"]) if row else (-1, GENESIS)

    def append(self, event: Event) -> str:
        """Append an event, returning its chain hash.

        Rejects out-of-order or duplicate seq numbers outright. A log that tolerates gaps
        cannot support a determinism claim.
        """
        last_seq, prev = self.head(event.task_id)
        if event.seq != last_seq + 1:
            raise ValueError(
                f"audit: non-contiguous seq for {event.task_id}: "
                f"expected {last_seq + 1}, got {event.seq}"
            )
        h = _link(prev, event.hashable())
        self.conn.execute(
            "INSERT INTO events (task_id, seq, ts, type, payload_json, audit_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event.task_id, event.seq, event.ts, str(event.type),
             canonical_json(event.payload), h),
        )
        return h

    # ---------------------------------------------------------------- reading --
    def events(self, task_id: str) -> list[Event]:
        import json

        return [
            Event(
                task_id=r["task_id"], seq=r["seq"], ts=r["ts"],
                type=EventType(r["type"]), payload=json.loads(r["payload_json"]),
            )
            for r in self.conn.execute(
                "SELECT * FROM events WHERE task_id = ? ORDER BY seq", (task_id,)
            )
        ]

    def hashes(self, task_id: str) -> list[str]:
        return [
            r["audit_hash"]
            for r in self.conn.execute(
                "SELECT audit_hash FROM events WHERE task_id = ? ORDER BY seq", (task_id,)
            )
        ]

    def tasks(self) -> list[str]:
        return [r["task_id"] for r in self.conn.execute(
            "SELECT DISTINCT task_id FROM events ORDER BY task_id")]

    # -------------------------------------------------------------- integrity --
    def verify_chain(self, task_id: str) -> tuple[bool, str | None]:
        """Recompute the whole chain. Returns (ok, first_bad_description)."""
        prev = GENESIS
        rows = list(self.conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY seq", (task_id,)))
        import json

        for i, r in enumerate(rows):
            if r["seq"] != i:
                return False, f"seq gap at index {i}: found {r['seq']}"
            ev = Event(task_id=r["task_id"], seq=r["seq"], ts=r["ts"],
                       type=EventType(r["type"]), payload=json.loads(r["payload_json"]))
            expect = _link(prev, ev.hashable())
            if expect != r["audit_hash"]:
                return False, (
                    f"hash mismatch at seq {r['seq']}: stored {r['audit_hash'][:12]}…, "
                    f"recomputed {expect[:12]}…"
                )
            prev = r["audit_hash"]
        return True, None
