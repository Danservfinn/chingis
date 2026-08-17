"""Outcomes must reach the database, or the founding comparison has nothing to read.

`DecisionLog.record_outcome` existed, was tested, and was **called from nowhere in
production**. `outcomes` therefore had zero rows, `m0_control.py --compare` always read
an empty Chingis arm, and M0 vs Chingis -- the experiment the whole project's founding
claim rests on -- could not be run end to end. It had never been run, and this is why.

The second property here is subtler and matters more. Success-per-dollar is M0's metric,
and lanes do not all report dollars: WR meters tokens and reports `usd`; W3 is billed by
a provider that does not expose per-call cost, so it reports none. Recording a missing
cost as 0.0 would make an unpriced lane look infinitely efficient and hand Chingis a win
it did not earn. Unknown must stay NULL and be counted separately.
"""

from __future__ import annotations

from cli import record_outcomes
from kernel.db import connect, migrate


class _Ev:
    def __init__(self, type_, payload):
        self.type, self.payload = type_, payload


def _seed_contract(conn, cid="c_0001", fleet="WR"):
    conn.execute(
        "INSERT OR IGNORE INTO contracts (id, task_id, fleet, spec_json, status, created_ts)"
        " VALUES (?,?,?,?,?,datetime('now'))", (cid, "t_0", fleet, "{}", "done"))


def test_a_successful_contract_lands_in_outcomes(tmp_path):
    conn = connect(tmp_path / "c.db"); migrate(conn)
    _seed_contract(conn)
    n = record_outcomes(conn, [_Ev("worker_done", {
        "contract_id": "c_0001", "lane": "WR", "wall_s": 12.5,
        "cost": {"usd": 0.031, "quota_units": 1.0},
        "v1": {"pass": True, "checks": []}})])
    assert n == 1
    row = conn.execute("SELECT * FROM outcomes WHERE contract_id='c_0001'").fetchone()
    assert row["v1_pass"] == 1
    assert abs(row["cost_usd"] - 0.031) < 1e-9
    assert abs(row["wall_s"] - 12.5) < 1e-9
    conn.close()


def test_a_failed_verification_is_recorded_as_a_failure_not_skipped(tmp_path):
    """A contract that ran and failed V1 is data. Dropping it would silently inflate the
    pass rate of whichever arm failed more often."""
    conn = connect(tmp_path / "c.db"); migrate(conn)
    _seed_contract(conn)
    record_outcomes(conn, [_Ev("verify_failed", {
        "contract_id": "c_0001", "lane": "WR", "wall_s": 4.0,
        "cost": {"usd": 0.01}, "detail": {"pass": False, "checks": []}})])
    assert conn.execute("SELECT v1_pass FROM outcomes").fetchone()["v1_pass"] == 0
    conn.close()


def test_an_unpriced_lane_records_null_cost_not_zero(tmp_path):
    """W3 is billed by a provider that reports no per-call cost. Recording that as 0.0
    would give it an infinite success-per-dollar and flatter whichever arm used it."""
    conn = connect(tmp_path / "c.db"); migrate(conn)
    _seed_contract(conn, fleet="W3")
    record_outcomes(conn, [_Ev("worker_done", {
        "contract_id": "c_0001", "lane": "W3", "wall_s": 20.0,
        "cost": {"wall_s": 20.0},                       # no usd key at all
        "v1": {"pass": True, "checks": []}})])
    row = conn.execute("SELECT cost_usd FROM outcomes").fetchone()
    assert row["cost_usd"] is None, "unknown cost must stay NULL, never 0.0"
    conn.close()


def test_one_row_per_contract_even_after_a_retry(tmp_path):
    """A retried contract emits several terminal events. The outcome is the LAST word,
    not one row per attempt -- otherwise a lane that retries a lot looks more productive."""
    conn = connect(tmp_path / "c.db"); migrate(conn)
    _seed_contract(conn)
    n = record_outcomes(conn, [
        _Ev("verify_failed", {"contract_id": "c_0001", "lane": "WR", "wall_s": 3.0,
                              "cost": {"usd": 0.01}, "detail": {"pass": False}}),
        _Ev("worker_done", {"contract_id": "c_0001", "lane": "WR", "wall_s": 9.0,
                            "cost": {"usd": 0.02}, "v1": {"pass": True}}),
    ])
    assert n == 1
    rows = conn.execute("SELECT v1_pass, cost_usd FROM outcomes").fetchall()
    assert len(rows) == 1 and rows[0]["v1_pass"] == 1
    conn.close()


def test_w3_contracts_can_be_persisted_at_all(tmp_path):
    """The `contracts.fleet` CHECK shipped as (WR, W1, W2, W0) and W3 was added as a lane
    months later without it. Every W3 contract failed the constraint, so it could not be
    stored -- and because `outcomes` has a foreign key onto `contracts`, no W3 result
    could be recorded either. Migration 002 fixes it; this pins it."""
    conn = connect(tmp_path / "c.db"); migrate(conn)
    _seed_contract(conn, cid="c_w3", fleet="W3")
    assert conn.execute("SELECT fleet FROM contracts WHERE id='c_w3'").fetchone()["fleet"] == "W3"
    conn.close()


def test_events_without_a_contract_are_ignored(tmp_path):
    conn = connect(tmp_path / "c.db"); migrate(conn)
    assert record_outcomes(conn, [_Ev("task_received", {"objective": "x"})]) == 0
    conn.close()
