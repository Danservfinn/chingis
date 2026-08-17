"""Contract persistence. Plan §2, §3.

Contracts are the unit of everything: dispatch, retry, racing, audit, and eventually
distillation. Holding them only in memory means a task cannot be resumed and the loop has
no record of what was actually asked -- which would make `outcomes` labels for questions
nobody kept.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "contract.schema.json"

VALID_LANES = {"WR", "W1", "W2", "W0"}


class ContractInvalid(ValueError):
    pass


def validate(contract: dict) -> dict:
    """Kernel-side re-validation against contract.schema.json.

    Same posture as decisions: the schema constrains the executive, and the kernel checks
    anyway. `capabilities` is checked hardest -- it is the field that decides what a worker
    can touch.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    for key in schema["required"]:
        if key not in contract:
            raise ContractInvalid(f"missing required field {key!r}")
    if contract["lane"] not in VALID_LANES:
        raise ContractInvalid(f"unknown lane {contract['lane']!r}")
    if not str(contract.get("objective", "")).strip():
        raise ContractInvalid("objective must be a non-empty paragraph")
    if not contract.get("context_refs"):
        raise ContractInvalid("context_refs must name at least one path -- least privilege")

    import re
    pat = re.compile(schema["properties"]["capabilities"]["items"]["pattern"])
    for cap in contract["capabilities"]:
        if not pat.match(cap):
            raise ContractInvalid(f"unrecognised capability {cap!r}")

    budget = contract.get("budget") or {}
    for field in ("wall_min", "quota_units", "tokens_usd", "tool_calls", "inlane_retries"):
        if field not in budget:
            raise ContractInvalid(f"budget missing {field!r}; unmetered dispatch is not allowed")
    if float(budget["wall_min"]) <= 0:
        raise ContractInvalid("wall_min must be positive")
    return contract


class ContractStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def row_id(task_id: str, contract_id: str) -> str:
        """The stored key: the executive's contract name, qualified by its task.

        The executive names contracts LOCALLY -- observed live on 2026-08-17, it called
        every single one `c_0001`. `contracts.id` is a PRIMARY KEY, so four separate
        tasks collapsed into one row and each overwrote the last; `outcomes` has a
        foreign key onto it and collapsed with it. Per-contract accounting was therefore
        capped at however many distinct names the model happened to invent, which is not
        a number any experiment should depend on.

        Qualifying by task makes the key unique without asking the model to be careful,
        which is the right place for the fix: a naming convention the executive must
        remember is a naming convention it will eventually forget.
        """
        return contract_id if contract_id.startswith(f"{task_id}:") else f"{task_id}:{contract_id}"

    def put(self, contract: dict, task_id: str, ts: str, status: str = "pending") -> str:
        validate(contract)
        rid = self.row_id(task_id, contract["contract_id"])
        self.conn.execute(
            "INSERT OR REPLACE INTO contracts (id, task_id, fleet, spec_json, status, created_ts)"
            " VALUES (?,?,?,?,?,?)",
            (rid, task_id, contract["lane"],
             json.dumps(contract, sort_keys=True), status, ts),
        )
        return rid

    def set_status(self, contract_id: str, status: str) -> None:
        self.conn.execute("UPDATE contracts SET status = ? WHERE id = ?", (status, contract_id))

    def get(self, contract_id: str) -> dict | None:
        row = self.conn.execute("SELECT spec_json FROM contracts WHERE id = ?",
                                (contract_id,)).fetchone()
        return json.loads(row["spec_json"]) if row else None

    def for_task(self, task_id: str) -> list[dict]:
        return [json.loads(r["spec_json"]) for r in self.conn.execute(
            "SELECT spec_json FROM contracts WHERE task_id = ? ORDER BY created_ts", (task_id,))]

    def by_status(self, status: str) -> list[str]:
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM contracts WHERE status = ?", (status,))]

    def next_id(self, task_id: str) -> str:
        n = self.conn.execute("SELECT COUNT(*) n FROM contracts WHERE task_id = ?",
                              (task_id,)).fetchone()["n"]
        return f"c_{n:04d}"
