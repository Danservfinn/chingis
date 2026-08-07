"""Advantage-filtered behaviour cloning + light RL prep. Spec §10.

Turns logged (ledger_snapshot, event, decision, outcome) rows into a training set for the
Phase-3 local executive. The teacher bootstraps; outcome signal carries the student past
imitation. Every metered dollar still buys training data.

Held-out discipline: the loop never trains on anything whose hash appears in evals/heldout/.
That check runs here, before any example is emitted, rather than being remembered later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .score import advantage_filter, score_outcome

ROOT = Path(__file__).resolve().parent.parent
HELDOUT = ROOT / "evals" / "heldout"


def heldout_hashes() -> set[str]:
    """Manifest hashes the loop must never train on."""
    out: set[str] = set()
    for manifest in HELDOUT.glob("*.manifest"):
        for line in manifest.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                out.add(line.split()[0])
    return out


def example_hash(ledger_hash: str, event_type: str) -> str:
    return hashlib.sha256(f"{ledger_hash}:{event_type}".encode()).hexdigest()[:16]


def build_dataset(conn: sqlite3.Connection, *, quantile: float = 0.6) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        "SELECT d.*, e.type event_type, e.payload_json, o.v1_pass, o.v1_detail_json,"
        "       o.v2_verdict, o.v3_verdict, o.cost_usd"
        " FROM decisions d JOIN events e ON e.id = d.event_id"
        " LEFT JOIN outcomes o ON o.contract_id = json_extract(d.params_json, '$.contract_id')")]

    excluded = heldout_hashes()
    scored, skipped = [], 0
    for r in rows:
        if example_hash(r["ledger_hash"], r["event_type"]) in excluded:
            skipped += 1
            continue
        outcome = {"v1_pass": r.get("v1_pass"),
                   "v1_detail": json.loads(r.get("v1_detail_json") or "{}"),
                   "v2_verdict": r.get("v2_verdict"), "v3_verdict": r.get("v3_verdict"),
                   "cost_usd": r.get("cost_usd")}
        scored.append((r, score_outcome(outcome)))

    kept = advantage_filter(scored, quantile=quantile)
    return [{"messages": [
                {"role": "system", "content": "ledger_hash=" + k["ledger_hash"]},
                {"role": "user", "content": f"event: {k['event_type']}\n{k['payload_json']}"},
                {"role": "assistant", "content": json.dumps(
                    {"decision": k["verb"], "reason_code": k["reason_code"],
                     "confidence": k["confidence"], "params": json.loads(k["params_json"])})},
            ], "_heldout_skipped": skipped}
            for k in kept]
