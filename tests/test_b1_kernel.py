"""B1 acceptance.

Plan §5: "pytest replays a 50-event scripted session byte-deterministically from the audit
log; capability denial and 60/85/100 budget thresholds fire correctly."

No adapters, no executive -- scripted fake events only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kernel.audit import AuditLog
from kernel.bus import Bus
from kernel.capabilities import CapabilityDenied, Registry, parse_contract_capabilities, safe_join
from kernel.db import connect, migrate
from kernel.deps import Deps
from kernel.events import EventType
from kernel.meters import BudgetExceeded, Meters, QuotaLedger
from kernel.replay import replay_task, rerun_matches


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    yield c
    c.close()


SCRIPT = [
    (EventType.TASK_RECEIVED, {"objective": "scripted session"}),
    *[(EventType.WORKER_DONE, {"contract_id": f"c_{i:04d}", "artifact": "diff"}) for i in range(16)],
    *[(EventType.WORKER_FAILED, {"contract_id": f"c_{i:04d}", "exit": 1}) for i in range(16, 24)],
    *[(EventType.VERIFY_FAILED, {"contract_id": f"c_{i:04d}", "tier": "V1"}) for i in range(24, 32)],
    *[(EventType.WORKER_REFUSED, {"contract_id": f"c_{i:04d}"}) for i in range(32, 36)],
    *[(EventType.BUDGET_THRESHOLD, {"meter": "tokens_usd", "threshold": t}) for t in (60, 85)],
    *[(EventType.QUOTA_THRESHOLD, {"fleet": "W2", "burn": i}) for i in range(38, 41)],
    (EventType.POLICY_EXCEPTION, {"reason": "no rule matched"}),
    (EventType.TIMEOUT, {"contract_id": "c_0044"}),
    (EventType.HUMAN_INPUT, {"message": "status?"}),
    (EventType.DECISION_INVALID, {"attempt": 1}),
    *[(EventType.WORKER_DONE, {"contract_id": f"c_{i:04d}", "artifact": "diff"}) for i in range(45, 49)],
]
assert len(SCRIPT) == 50, f"the scripted session must be 50 events, got {len(SCRIPT)}"


def _emit_script(bus: Bus) -> None:
    for etype, payload in SCRIPT:
        bus.emit(etype, payload)


# ------------------------------------------------------------------ replay ----
def test_scripted_session_is_50_events(conn):
    bus = Bus("t_repl", AuditLog(conn), Deps.deterministic([f"2026-08-06T12:00:{i:02d}Z" for i in range(60)]))
    _emit_script(bus)
    assert len(AuditLog(conn).events("t_repl")) == 50


def test_replay_is_byte_deterministic(conn):
    stamps = [f"2026-08-06T12:00:{i:02d}Z" for i in range(60)]
    bus = Bus("t_repl", AuditLog(conn), Deps.deterministic(stamps))
    _emit_script(bus)

    # 1. The stored chain verifies against the stored bodies.
    result = replay_task(conn, "t_repl")
    assert result.ok, str(result)
    assert result.events == 50

    # 2. Re-executing with log-driven Deps reproduces the chain hash-for-hash.
    rerun = rerun_matches(conn, "t_repl", _emit_script)
    assert rerun.ok, str(rerun)


def test_replay_detects_tampering(conn):
    stamps = [f"2026-08-06T12:00:{i:02d}Z" for i in range(60)]
    bus = Bus("t_tamper", AuditLog(conn), Deps.deterministic(stamps))
    _emit_script(bus)
    assert replay_task(conn, "t_tamper").ok

    # Flip one byte deep inside the log -- the exact thing the hash chain exists to catch.
    conn.execute(
        "UPDATE events SET payload_json = ? WHERE task_id = ? AND seq = ?",
        ('{"contract_id":"c_0005","artifact":"TAMPERED"}', "t_tamper", 6),
    )
    bad = replay_task(conn, "t_tamper")
    assert not bad.ok
    assert bad.diverged_at == 6, f"should point at the tampered event, said {bad.diverged_at}"


def test_audit_rejects_noncontiguous_seq(conn):
    from kernel.events import Event

    audit = AuditLog(conn)
    audit.append(Event("t_gap", 0, "2026-08-06T12:00:00Z", EventType.TASK_RECEIVED, {}))
    with pytest.raises(ValueError, match="non-contiguous"):
        audit.append(Event("t_gap", 5, "2026-08-06T12:00:01Z", EventType.WORKER_DONE, {}))


def test_chain_is_per_task(conn):
    d = Deps.deterministic([f"2026-08-06T12:00:{i:02d}Z" for i in range(20)])
    a, b = Bus("t_a", AuditLog(conn), d), Bus("t_b", AuditLog(conn), d)
    a.emit(EventType.TASK_RECEIVED, {"x": 1})
    b.emit(EventType.TASK_RECEIVED, {"x": 1})
    # Same payload, same type, different task -> different chain position, and both verify.
    assert replay_task(conn, "t_a").ok and replay_task(conn, "t_b").ok


# ------------------------------------------------------------- capabilities ----
def test_capability_denies_unlisted_tool():
    reg = Registry(Deps.deterministic())
    cap = reg.mint("W1", tools={"read_file"}, path_prefix="/tmp/wt")
    reg.check_tool(cap.token, "read_file")
    with pytest.raises(CapabilityDenied, match="may not call"):
        reg.check_tool(cap.token, "bash")
    assert reg.denials[-1]["kind"] == "tool"


def test_capability_denies_path_outside_worktree(tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / "in.txt").write_text("ok")
    outside = tmp_path / "secret.txt"
    outside.write_text("no")

    reg = Registry(Deps.deterministic())
    cap = reg.mint("WR", tools={"read_file"}, path_prefix=wt)
    reg.check_path(cap.token, wt / "in.txt")
    with pytest.raises(CapabilityDenied, match="may not read"):
        reg.check_path(cap.token, outside)


def test_traversal_and_symlink_escape_are_denied(tmp_path):
    """The B2 containment requirement, enforced at the kernel layer it belongs in:
    an escape is denied by the capability check, not by model politeness."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    (tmp_path / "secret.txt").write_text("employer data")

    with pytest.raises(CapabilityDenied, match="escapes the worktree"):
        safe_join(wt, "../secret.txt")
    with pytest.raises(CapabilityDenied):
        safe_join(wt, "a/b/../../../secret.txt")
    with pytest.raises(CapabilityDenied):
        safe_join(wt, "/etc/passwd")

    (wt / "escape").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(CapabilityDenied, match="escapes the worktree"):
        safe_join(wt, "escape")


def test_executive_cannot_widen_its_own_capability():
    """The registry is kernel-only. A Capability is frozen, so a model holding one cannot
    mutate its scope even if it somehow got a reference."""
    reg = Registry(Deps.deterministic())
    cap = reg.mint("E", tools={"read_file"}, path_prefix="/tmp/wt")
    with pytest.raises((AttributeError, TypeError)):
        cap.tools = frozenset({"bash"})  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        cap.path_prefix = Path("/")  # type: ignore[misc]


def test_contract_capabilities_parse(tmp_path):
    reg = Registry(Deps.deterministic())
    cap = parse_contract_capabilities(reg, "WR", ["fs:worktree", "net:none", "proc:bash"], tmp_path)
    assert cap.allows_tool("bash") and cap.allows_tool("apply_patch")
    assert not cap.allows_net("api.openai.com")
    assert cap.allows_path(tmp_path / "x")


# ------------------------------------------------------------------ meters ----
def test_thresholds_fire_at_60_85_100_exactly_once():
    m = Meters(usd=1.0, quota_units=100)
    assert m.charge(usd=0.50) == []                                    # 50%
    assert [e["threshold"] for e in m.charge(usd=0.15)] == [60]        # 65%
    assert m.charge(usd=0.10) == []                                    # 75%, no new crossing
    assert [e["threshold"] for e in m.charge(usd=0.15)] == [85]        # 90%
    fired = m.charge(usd=0.10)                                         # 100%
    assert [e["threshold"] for e in fired] == [100]
    assert fired[0]["hard_halt"] is True
    assert m.charge(usd=0.50) == [], "thresholds must not re-fire"


def test_single_charge_can_cross_several_thresholds():
    m = Meters(usd=1.0, quota_units=10)
    assert [e["threshold"] for e in m.charge(usd=1.0)] == [60, 85, 100]


def test_preflight_refuses_before_dispatch():
    """Checked BEFORE dispatch, not after. Hard stops beat apologetic overruns."""
    m = Meters(usd=1.0, quota_units=10)
    m.charge(usd=0.9)
    m.preflight(usd=0.05)
    with pytest.raises(BudgetExceeded, match="tokens_usd"):
        m.preflight(usd=0.5)
    with pytest.raises(BudgetExceeded, match="quota_units"):
        m.preflight(quota=99)
    assert m.usd.spent == pytest.approx(0.9), "a refused preflight must not charge"


def test_quota_burn_rate_anomaly():
    q = QuotaLedger()
    for i in range(24):                       # steady baseline over 6h
        q.record("W2", 1.0, at_s=i * 900)
    assert not q.anomalous("W2", now_s=24 * 900)
    for i in range(40):                       # sudden spike in the last 15 min
        q.record("W2", 5.0, at_s=24 * 900 + i)
    assert q.anomalous("W2", now_s=24 * 900 + 60)


# -------------------------------------------------------------------- bus ----
def test_operator_input_outranks_everything():
    assert EventType.HUMAN_INPUT.__class__ is EventType
    from kernel.events import PRIORITY
    assert PRIORITY[EventType.HUMAN_INPUT] < min(
        v for k, v in PRIORITY.items() if k is not EventType.HUMAN_INPUT
    )


def test_every_event_wakes_the_executive(conn):
    """The kernel forwards, it does not filter."""
    woken: list[EventType] = []
    bus = Bus("t_wake", AuditLog(conn), Deps.deterministic([f"2026-08-06T12:00:{i:02d}Z" for i in range(60)]))
    bus.on_any(lambda e, b: woken.append(e.type))
    _emit_script(bus)
    asyncio.run(bus.run())
    assert len(woken) == 50


def test_never_reflexable_events():
    """A reflex must never be able to swallow an operator message or a hard budget stop."""
    from kernel.events import Event

    for t in (EventType.HUMAN_INPUT, EventType.BUDGET_THRESHOLD,
              EventType.DECISION_INVALID, EventType.POLICY_EXCEPTION):
        assert not Event("t", 0, "ts", t, {}).reflexable
    assert Event("t", 0, "ts", EventType.WORKER_DONE, {}).reflexable


def test_runaway_handler_is_caught(conn):
    bus = Bus("t_loop", AuditLog(conn))
    bus.on(EventType.WORKER_DONE, lambda e, b: b.emit(EventType.WORKER_DONE, {}))
    bus.emit(EventType.WORKER_DONE, {})
    with pytest.raises(RuntimeError, match="emitting in a cycle"):
        asyncio.run(bus.run(max_events=50))
