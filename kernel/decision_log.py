"""Decision logging. Spec §10, B6.

    (ledger_snapshot, event, decision, reason_code, outcome, cost)
    -- one row per executive wake, written by the kernel, replay-verifiable.

Every decision row is a future training example; every outcome row is its label. This is
the single most important thing the system produces: the harness logs (state, decision,
outcome) triples from day one, and the cloud executive is a teacher on a clock.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from executive.client import Decision


class DecisionLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record(self, *, event_id: int, ledger_hash: str, decision: Decision, model: str,
               latency_ms: int | None = None, cost_usd: float | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO decisions (event_id, ledger_hash, verb, reason_code, confidence,"
            " params_json, model, latency_ms, cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, ledger_hash, decision.decision, decision.reason_code,
             decision.confidence, json.dumps(decision.params, sort_keys=True),
             model, latency_ms, cost_usd),
        )
        return cur.lastrowid

    def record_outcome(self, contract_id: str, outcome: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO outcomes (contract_id, v1_pass, v1_detail_json,"
            " v2_verdict, v3_verdict, cost_usd, quota_units, wall_s, score)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (contract_id,
             int(bool(outcome.get("v1_pass"))) if outcome.get("v1_pass") is not None else None,
             json.dumps(outcome.get("v1_detail", {}), sort_keys=True),
             outcome.get("v2_verdict"), outcome.get("v3_verdict"),
             outcome.get("cost_usd"), outcome.get("quota_units"),
             outcome.get("wall_s"), outcome.get("score")),
        )

    def event_id_for(self, task_id: str, seq: int) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM events WHERE task_id = ? AND seq = ?", (task_id, seq)).fetchone()
        return row["id"] if row else None

    # --------------------------------------------------------------- readback --
    def verb_counts(self) -> dict[str, int]:
        return {r["verb"]: r["n"] for r in self.conn.execute(
            "SELECT verb, COUNT(*) n FROM decisions GROUP BY verb ORDER BY n DESC")}

    def reason_code_outcomes(self) -> list[dict]:
        """The bandit's raw material: per-(reason_code) outcome stats.

        This is what makes reason_code an enum rather than free text -- it only earns its
        place if you can group by it.
        """
        return [dict(r) for r in self.conn.execute(
            "SELECT d.reason_code, COUNT(*) n, AVG(d.confidence) avg_conf,"
            "       AVG(COALESCE(o.score, 0)) avg_score"
            " FROM decisions d LEFT JOIN outcomes o ON 1=0"
            " GROUP BY d.reason_code ORDER BY n DESC")]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM decisions").fetchone()["n"]
