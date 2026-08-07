"""Deterministic re-run from the audit log. B1's acceptance criterion.

A replay re-derives the hash chain from the recorded event bodies and compares it to what
was stored. If a single byte of any payload, timestamp, or type differs, the chain diverges
at that event and `replay_task` reports the exact seq.

This is a stronger claim than "the code produces the same output": it verifies that the log
is a faithful, tamper-evident record of the run, which is what the improvement loop is
later going to train on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .audit import GENESIS, AuditLog, _link
from .deps import Deps
from .events import Event


@dataclass
class ReplayResult:
    task_id: str
    events: int
    ok: bool
    diverged_at: int | None = None
    detail: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"replay {self.task_id}: {self.events} events, chain intact"
        return f"replay {self.task_id}: DIVERGED at seq {self.diverged_at} -- {self.detail}"


def replay_task(conn: sqlite3.Connection, task_id: str) -> ReplayResult:
    """Recompute the chain for one task from its stored event bodies."""
    audit = AuditLog(conn)
    events = audit.events(task_id)
    stored = audit.hashes(task_id)

    prev = GENESIS
    for i, (event, want) in enumerate(zip(events, stored)):
        if event.seq != i:
            return ReplayResult(task_id, len(events), False, i,
                                f"expected seq {i}, log has {event.seq}")
        got = _link(prev, event.hashable())
        if got != want:
            return ReplayResult(
                task_id, len(events), False, event.seq,
                f"recomputed {got[:16]}… != stored {want[:16]}…",
            )
        prev = want
    return ReplayResult(task_id, len(events), True)


def replay_all(conn: sqlite3.Connection) -> list[ReplayResult]:
    return [replay_task(conn, t) for t in AuditLog(conn).tasks()]


def deps_for_replay(conn: sqlite3.Connection, task_id: str) -> Deps:
    """Deps that reproduce the recorded run: timestamps come from the log, ids are counted.

    Re-running handlers with these must regenerate byte-identical events. If the code has
    changed in a way that alters what it emits, the re-run's chain will not match -- which
    is the point. A replay that adapts to new code is not a replay.
    """
    stamps = [e.ts for e in AuditLog(conn).events(task_id)]
    return Deps.deterministic(stamps)


def rerun_matches(
    conn: sqlite3.Connection, task_id: str, rebuild: "callable"
) -> ReplayResult:
    """Re-execute a task with log-driven Deps and compare the resulting chain.

    `rebuild(bus)` must re-emit the same events the original run did. Used by the B1
    scripted-session test.
    """
    from .bus import Bus
    from .db import connect, migrate

    original = AuditLog(conn).hashes(task_id)
    stamps = [e.ts for e in AuditLog(conn).events(task_id)]

    # Replay into a scratch database under the SAME task_id. The id is part of the hashed
    # body, so replaying under a shadow id would diverge at seq 0 for a reason that has
    # nothing to do with determinism.
    scratch = connect(":memory:")
    try:
        migrate(scratch)
        bus = Bus(task_id, AuditLog(scratch), Deps.deterministic(stamps), replay=True)
        rebuild(bus)
        replayed = AuditLog(scratch).hashes(task_id)
    finally:
        scratch.close()

    if len(original) != len(replayed):
        return ReplayResult(task_id, len(original), False, min(len(original), len(replayed)),
                            f"event count {len(original)} -> {len(replayed)}")
    for i, (a, b) in enumerate(zip(original, replayed)):
        if a != b:
            return ReplayResult(task_id, len(original), False, i,
                                f"chain diverged: {a[:16]}… != {b[:16]}…")
    return ReplayResult(task_id, len(original), True)
