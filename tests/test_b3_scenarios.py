"""B3 acceptance: the ten scripted orchestration scenarios.

Plan §5: "All 10 scenarios green: clean dispatch · fail->retry ok · fail x2->reroute ·
refusal->reroute · refusal x2->request_human · budget 85%->checkpoint/descope ·
verify_failed->revised retry · peak-window route override · outage->drain ·
ambiguous->request_human. Stack freeze lifts here."

These test the KERNEL's handling of decisions against known event sequences -- not model
quality. The executive is scripted so each scenario is an assertion rather than a vibe.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from adapters.base import Result, Status
from executive.client import ScriptedExecutive, validate
from executive.ledger import Ledger
from kernel.audit import AuditLog
from kernel.db import connect, migrate
from kernel.deps import Deps
from kernel.events import EventType
from kernel.meters import Meters
from kernel.policy_runtime import PolicyRuntime
from kernel.runtime import Runtime


# ------------------------------------------------------------------ fixtures ----
class FakeAdapter:
    """A lane whose outcomes the scenario dictates."""

    def __init__(self, lane: str, outcomes: list[Result] | None = None,
                 default: Result | None = None) -> None:
        self.lane = lane
        self.outcomes = list(outcomes or [])
        self.default = default or Result(Status.DONE, {"diff": "ok"}, {"usd": 0.001, "quota_units_est": 1.0})
        self.runs: list[dict] = []

    def healthcheck(self):
        return True, "fake"

    def run(self, contract, worktree: Path) -> Result:
        self.runs.append(contract)
        r = self.outcomes.pop(0) if self.outcomes else self.default
        r.lane = self.lane
        return r


def dec(verb, reason="lane_capability_fit", conf=0.9, **params):
    return {"schema_version": 1, "decision": verb, "reason_code": reason,
            "confidence": conf, "params": params}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "s.db")
    migrate(c)
    yield c
    c.close()


def build(conn, tmp_path, script, adapters, *, usd=1.0, quota=100.0, policy=None, ledger=None):
    stamps = [f"2026-08-06T{12 + i // 60:02d}:{i % 60:02d}:00Z" for i in range(400)]
    rt = Runtime(
        "t_sc", ScriptedExecutive(script), adapters,
        audit=AuditLog(conn), meters=Meters(usd=usd, quota_units=quota),
        deps=Deps.deterministic(stamps), policy=policy, ledger=ledger,
        workdir=tmp_path / "wt",
    )
    return rt


def types_of(rt):
    return [str(e.type) for e in rt.bus.processed]


# ---------------------------------------------------------------- 1. clean ----
def test_01_clean_dispatch(conn, tmp_path):
    rt = build(conn, tmp_path, [dec("dispatch", lane="WR", contract_id="c_0001"),
                                dec("halt", "terminal_success", status="success")],
               {"WR": FakeAdapter("WR")})
    rt.submit("add a docstring")
    asyncio.run(rt.run())
    assert "worker_done" in types_of(rt)
    assert rt.state.halted_status == "success"


# ------------------------------------------------------- 2. fail -> retry ok ----
def test_02_fail_then_retry_succeeds(conn, tmp_path):
    wr = FakeAdapter("WR", outcomes=[
        Result(Status.FAILED, {"error": "boom"}, {"usd": 0.001}),
        Result(Status.DONE, {"diff": "fixed"}, {"usd": 0.001}),
    ])
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("retry", "verify_failed_revise", contract_id="c_0001", lane="WR"),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": wr})
    rt.submit("thing")
    asyncio.run(rt.run())
    t = types_of(rt)
    assert "worker_failed" in t and "worker_done" in t
    assert rt.state.retries["c_0001"] == 1


# --------------------------------------------------- 3. fail x2 -> reroute ----
def test_03_two_failures_reroute_to_other_lane(conn, tmp_path):
    wr = FakeAdapter("WR", default=Result(Status.FAILED, {"error": "always"}, {"usd": 0.001}))
    w2 = FakeAdapter("W2")
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("retry", contract_id="c_0001", lane="WR"),
        dec("route", "lane_healthcheck_failed", contract_id="c_0001", lane="W2"),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": wr, "W2": w2})
    rt.submit("thing")
    asyncio.run(rt.run())
    assert len(wr.runs) == 2 and len(w2.runs) == 1
    assert "worker_done" in types_of(rt)


# --------------------------------------------------- 4. refusal -> reroute ----
def test_04_refusal_reroutes_in_one_hop(conn, tmp_path):
    w1 = FakeAdapter("W1", default=Result(Status.REFUSED, {}, {"quota_units_est": 1.0},
                                          refusal_signal="I can't help with that"))
    w2 = FakeAdapter("W2")
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="W1", contract_id="c_0001"),
        dec("route", "refusal_reroute", contract_id="c_0001", lane="W2"),
        dec("halt", "terminal_success", status="success"),
    ], {"W1": w1, "W2": w2})
    rt.submit("crypto-adjacent thing")
    asyncio.run(rt.run())
    t = types_of(rt)
    assert "worker_refused" in t, "a refusal must surface as its own event, never a silent stall"
    assert t.index("worker_refused") < t.index("worker_done")


# ---------------------------------------- 5. refusal x2 -> request_human ----
def test_05_two_refusals_escalate_to_operator(conn, tmp_path):
    refuse = lambda: Result(Status.REFUSED, {}, {"quota_units_est": 1.0}, refusal_signal="I must decline")
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="W1", contract_id="c_0001"),
        dec("route", "refusal_reroute", contract_id="c_0001", lane="W2"),
        dec("request_human", "gate_decision_required",
            question="Both fleets refuse; is this task in scope?"),
    ], {"W1": FakeAdapter("W1", default=refuse()), "W2": FakeAdapter("W2", default=refuse())})
    rt.submit("doubly refused thing")
    asyncio.run(rt.run())
    assert types_of(rt).count("worker_refused") == 2
    assert rt.state.awaiting_human and "in scope" in rt.state.awaiting_human
    assert rt.bus.halted


# ------------------------------------- 6. budget 85% -> checkpoint/descope ----
def test_06_budget_85_triggers_checkpoint_and_descope(conn, tmp_path):
    wr = FakeAdapter("WR", default=Result(Status.DONE, {"diff": "d"}, {"usd": 0.9}))
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("checkpoint", "budget_threshold_descope"),
        dec("revise_plan", "budget_threshold_descope", plan=["descoped: ship the minimum"]),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": wr}, usd=1.0)
    rt.submit("expensive thing")
    asyncio.run(rt.run())
    thresholds = [e.payload.get("threshold") for e in rt.bus.processed
                  if e.type is EventType.BUDGET_THRESHOLD]
    assert 60 in thresholds and 85 in thresholds
    assert rt.state.checkpoints, "85% should have produced a resumable checkpoint"
    assert rt.ledger.plan == ["descoped: ship the minimum"]


def test_06b_hard_halt_at_100_percent(conn, tmp_path):
    """At 100 the task halts and the executive does not get a vote."""
    wr = FakeAdapter("WR", default=Result(Status.DONE, {"diff": "d"}, {"usd": 1.2}))
    rt = build(conn, tmp_path, [dec("dispatch", lane="WR", contract_id="c_0001"),
                                dec("dispatch", lane="WR", contract_id="c_0002")],
               {"WR": wr}, usd=1.0)
    rt.submit("runaway")
    asyncio.run(rt.run())
    assert rt.bus.halted and "100%" in (rt.bus.halt_reason or "")
    assert len(wr.runs) == 1, "no dispatch may follow a hard halt"


# ------------------------------------- 7. verify_failed -> revised retry ----
def test_07_verify_failed_produces_revised_retry(conn, tmp_path):
    class V1Fails:
        def __init__(self): self.n = 0
        def run_v1(self, contract, result):
            self.n += 1
            return {"pass": self.n > 1, "detail": "pytest: 1 failed" if self.n == 1 else "ok"}

    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("retry", "verify_failed_revise", contract_id="c_0001", lane="WR",
            objective="fix the failing assertion, do not touch the test"),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": FakeAdapter("WR")})
    rt.verifier = V1Fails()
    rt.submit("thing")
    asyncio.run(rt.run())
    t = types_of(rt)
    assert "verify_failed" in t and t.index("verify_failed") < t.index("worker_done")
    assert rt.state.contracts["c_0001"]["objective"].startswith("fix the failing")


# ------------------------------------------ 8. peak-window route override ----
def test_08_peak_window_reflex_overrides_route(conn, tmp_path):
    """The quota-arbitrage insight survives as an executive-authored reflex, not as law."""
    policy = PolicyRuntime(tmp_path / "p.yaml")
    policy.author({"match": {"event_type": "worker_done",
                             "window": "02:00-06:00 America/New_York"},
                   "action": {"lane": "W1", "contract_id": "c_0002"}, "ttl_days": 30},
                  decision_id="d_00412", reason_code="quota_peak_multiplier")

    w1, wr = FakeAdapter("W1"), FakeAdapter("WR")
    # All inside 06:00-10:00Z == 02:00-06:00 America/New_York, the GLM peak window.
    stamps = [f"2026-08-06T06:{m:02d}:{s:02d}Z" for m in range(30, 50) for s in range(0, 60, 10)]
    rt = Runtime("t_peak", ScriptedExecutive([
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("halt", "terminal_success", status="success"),
    ]), {"WR": wr, "W1": w1}, audit=AuditLog(conn), meters=Meters(usd=1, quota_units=100),
        deps=Deps.deterministic(stamps), policy=policy, workdir=tmp_path / "wt")
    rt.submit("routine coding")
    asyncio.run(rt.run())
    # 06:30Z == 02:30 America/New_York -> inside the peak window -> reflex fires.
    assert rt.reflex_hits, "the reflex should have short-circuited the worker_done wake"
    assert len(w1.runs) == 1


def test_08b_reflex_expires_without_reaffirmation(tmp_path):
    import datetime as dt
    now = dt.datetime(2026, 8, 6, tzinfo=dt.UTC)
    policy = PolicyRuntime(tmp_path / "p2.yaml", now_fn=lambda: now)
    policy.author({"match": {}, "action": {"lane": "W1"}, "ttl_days": 30},
                  decision_id="d_00001", reason_code="quota_peak_multiplier")
    assert len(policy.reflexes) == 1
    policy._now = lambda: now + dt.timedelta(days=31)
    assert policy.purge_expired() == 1
    assert policy.reflexes == [], "rigidity must decay by default"


def test_08c_developer_authored_reflex_is_rejected(tmp_path):
    from kernel.policy_runtime import PolicyError
    p = tmp_path / "bad.yaml"
    p.write_text("schema_version: 1\nreflexes:\n"
                 "- match: {}\n  action: {lane: W1}\n  author: developer\n"
                 "  reason_code: x\n  created_by: d_00001\n  ttl_days: 30\n")
    with pytest.raises(PolicyError, match="developer-authored"):
        PolicyRuntime(p)


# ------------------------------------------------------ 9. outage -> drain ----
def test_09_outage_drains_lane(conn, tmp_path):
    wr, w2 = FakeAdapter("WR"), FakeAdapter("W2")
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("route", "outage_drain", contract_id="c_0001", lane="W2"),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": wr, "W2": w2})
    rt.submit("thing")
    rt.drain("WR")
    asyncio.run(rt.run())
    assert len(wr.runs) == 0, "a drained lane must not receive dispatches"
    assert len(w2.runs) == 1
    assert "policy_exception" in types_of(rt)


# ------------------------------------------ 10. ambiguous -> request_human ----
def test_10_ambiguity_asks_the_operator(conn, tmp_path):
    rt = build(conn, tmp_path, [
        dec("request_human", "ambiguous_objective", conf=0.95,
            question="'Make it faster' -- latency or throughput?"),
    ], {"WR": FakeAdapter("WR")})
    rt.submit("make it faster")
    asyncio.run(rt.run())
    assert rt.state.awaiting_human
    assert rt.ledger.open_questions
    assert rt.bus.halted


# ------------------------------------------------------------- the gate ----
def test_all_ten_scenarios_present():
    """B3's criterion is all ten, not most."""
    import inspect, sys
    names = [n for n, _ in inspect.getmembers(sys.modules[__name__], inspect.isfunction)
             if n.startswith("test_") and n[5:7].isdigit()]
    assert len({n[5:7] for n in names}) == 10, f"expected 10 scenarios, found {sorted(names)}"


# ============================================ B4 gate: the full authoring cycle ====
def test_executive_authors_a_reflex_that_later_fires(conn, tmp_path):
    """B4's acceptance criterion, end to end.

    Plan §5: "E-authored reflex observed in audit with provenance + TTL."

    Everything before this tested the pieces: that a patch cannot claim developer
    authorship, that TTLs expire, that reflexes short-circuit. Nothing tested the loop the
    criterion actually names -- executive emits edit_policy, a reflex is created with
    stamped provenance, it survives to disk, and it then fires on a later matching event
    with its causing reflex recorded in the audit log.
    """
    policy = PolicyRuntime(tmp_path / "authored.yaml")
    wr, w1 = FakeAdapter("WR"), FakeAdapter("W1")
    stamps = [f"2026-08-06T06:{m:02d}:{s:02d}Z" for m in range(30, 50) for s in range(0, 60, 5)]

    rt = Runtime("t_b4", ScriptedExecutive([
        # 1. The executive notices a pattern and writes itself a reflex.
        dec("edit_policy", "quota_peak_multiplier", conf=0.9,
            policy_patch={"match": {"event_type": "worker_done"},
                          "action": {"lane": "W1", "contract_id": "c_0002"},
                          "ttl_days": 30}),
        # 2. Then dispatches normally. The resulting worker_done should hit the reflex.
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("halt", "terminal_success", status="success"),
    ]), {"WR": wr, "W1": w1}, audit=AuditLog(conn), meters=Meters(usd=5, quota_units=100),
        deps=Deps.deterministic(stamps), policy=policy, workdir=tmp_path / "wt")
    rt.submit("routine coding")
    # A second event is needed for the executive to act again: edit_policy emits nothing,
    # so without this the queue drains and the reflex never gets an event to match.
    rt.bus.emit(EventType.HUMAN_INPUT, {"message": "carry on"})
    asyncio.run(rt.run())

    # Provenance is stamped by the runtime, never supplied by the patch.
    assert len(policy.reflexes) == 1
    reflex = policy.reflexes[0]
    assert reflex.author == "executive"
    assert reflex.created_by.startswith("d_")
    assert reflex.ttl_days == 30
    assert reflex.created_ts.startswith("2026-08-06")

    # It survived to disk -- otherwise "observed in audit" dies with the process.
    reloaded = PolicyRuntime(tmp_path / "authored.yaml")
    assert reloaded.reflexes[0].created_by == reflex.created_by

    # It actually fired, and the audit log records WHICH reflex caused the effect.
    assert rt.reflex_hits, "the authored reflex never fired on a later event"
    assert rt.reflex_hits[0]["reflex"]["created_by"] == reflex.created_by
    assert len(w1.runs) == 1, "the reflex should have routed a dispatch to W1"

    caused = [e for e in rt.bus.processed if e.payload.get("_reflex")]
    assert caused, "no event carries the provenance of the reflex that caused it"
    assert caused[0].payload["_reflex"] == reflex.created_by

    # And the ledger records the authorship for the executive's own future context.
    assert any("authored reflex" in o for o in rt.ledger.recent_outcomes)


def test_a_reflex_the_executive_never_reaffirms_stops_firing(conn, tmp_path):
    """Rigidity decays by default. An expired reflex must fall back to waking the
    executive, not silently keep routing."""
    import datetime as dt
    now = dt.datetime(2026, 8, 6, tzinfo=dt.UTC)
    policy = PolicyRuntime(tmp_path / "decay.yaml", now_fn=lambda: now)
    policy.author({"match": {"event_type": "worker_done"}, "action": {"lane": "W1"},
                   "ttl_days": 30}, decision_id="d_00001", reason_code="quota_peak_multiplier")
    assert len(policy.reflexes) == 1

    policy._now = lambda: now + dt.timedelta(days=31)
    wr, w1 = FakeAdapter("WR"), FakeAdapter("W1")
    rt = Runtime("t_decay", ScriptedExecutive([
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("halt", "terminal_success", status="success"),
    ]), {"WR": wr, "W1": w1}, audit=AuditLog(conn), meters=Meters(usd=5, quota_units=100),
        deps=Deps.deterministic([f"2026-09-06T12:00:{i:02d}Z" for i in range(40)]),
        policy=policy, workdir=tmp_path / "wt")
    rt.submit("routine")
    asyncio.run(rt.run())
    assert not rt.reflex_hits, "an expired reflex still fired"
    assert len(w1.runs) == 0


# =========================== §11 failure table: rows that had no enforcement ====
def test_sustained_lane_failure_auto_drains(conn, tmp_path):
    """§11 'provider outage -> healthcheck drain'. drain() existed and only a human could
    reach it, so a dead provider kept receiving dispatches until someone noticed."""
    from kernel.runtime import LANE_FAILURES_BEFORE_DRAIN
    dead = FakeAdapter("WR", default=Result(Status.FAILED, {"error": "provider down"}, {"usd": 0.0}))
    alive = FakeAdapter("W2")
    script = [dec("dispatch", lane="WR", contract_id=f"c_{i:04d}")
              for i in range(LANE_FAILURES_BEFORE_DRAIN + 1)]
    script.append(dec("halt", "terminal_success", status="success"))

    rt = build(conn, tmp_path, script, {"WR": dead, "W2": alive})
    rt.submit("thing")
    asyncio.run(rt.run())

    assert "WR" in rt.state.drained_lanes, "a lane failing repeatedly was never drained"
    assert len(dead.runs) == LANE_FAILURES_BEFORE_DRAIN, \
        "dispatches continued to a lane already known dead"
    auto = [e for e in rt.bus.processed if e.payload.get("auto_drained")]
    assert auto, "the drain was not surfaced as an event the executive can react to"


def test_one_failure_then_success_does_not_drain(conn, tmp_path):
    """Drain is for sustained outage. Draining on a single bad contract would cost the
    executive an option for no reason -- and scenario 2 is exactly that shape."""
    flaky = FakeAdapter("WR", outcomes=[
        Result(Status.FAILED, {"error": "blip"}, {"usd": 0.0}),
        Result(Status.DONE, {"diff": "ok"}, {"usd": 0.0}),
        Result(Status.FAILED, {"error": "blip"}, {"usd": 0.0}),
    ])
    rt = build(conn, tmp_path, [
        dec("dispatch", lane="WR", contract_id="c_0001"),
        dec("retry", contract_id="c_0001", lane="WR"),
        dec("dispatch", lane="WR", contract_id="c_0002"),
        dec("halt", "terminal_success", status="success"),
    ], {"WR": flaky})
    rt.submit("thing")
    asyncio.run(rt.run())
    assert "WR" not in rt.state.drained_lanes, "a success must clear the failure streak"


def test_executive_latency_is_measured(conn, tmp_path):
    """§3 targets sub-2s routine decisions and §11 names latency stacking. The column
    existed; nothing wrote to it, so the failure mode was undetectable by construction."""
    rt = build(conn, tmp_path, [dec("dispatch", lane="WR", contract_id="c_0001"),
                                dec("halt", "terminal_success", status="success")],
               {"WR": FakeAdapter("WR")})
    rt.submit("thing")
    asyncio.run(rt.run())
    assert len(rt.decision_latencies) == len(rt.state.decisions)
    assert all(isinstance(x, int) and x >= 0 for x in rt.decision_latencies)
