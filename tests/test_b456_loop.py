"""B4 (policy), B5 (verification), B6 (logging/shadow/dashboard), and the loop's gates."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from executive.client import Decision
from executive.shadow import Agreement, ShadowExecutive
from kernel.audit import AuditLog
from kernel.db import connect, migrate
from kernel.decision_log import DecisionLog
from kernel.deps import Deps
from kernel.events import Event, EventType
from kernel.policy_runtime import PolicyError, PolicyRuntime, _in_window
from loop.promote import check_promotion
from loop.score import advantage_filter, score_outcome
from verify.v1_runners import diff_scope
from verify.v2_crossfleet import B0NotRun, build_prompt, parse_findings, require_b0_pass
from verify.v3_spotcheck import SpotChecker, sampled


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "x.db")
    migrate(c)
    yield c
    c.close()


# =============================================================== B4 policy ====
def test_reflex_provenance_is_stamped_not_supplied(tmp_path):
    """A patch claiming author=developer cannot launder itself into the adaptive plane."""
    p = PolicyRuntime(tmp_path / "p.yaml")
    r = p.author({"match": {}, "action": {"lane": "W1"}, "author": "developer", "ttl_days": 7},
                 decision_id="d_00007", reason_code="quota_headroom")
    assert r.author == "executive"
    assert r.created_by == "d_00007"


def test_immortal_reflex_refused(tmp_path):
    p = PolicyRuntime(tmp_path / "p.yaml")
    for ttl in (0, -5):
        with pytest.raises(PolicyError, match="immortal|positive"):
            p.author({"match": {}, "action": {"lane": "W1"}, "ttl_days": ttl},
                     decision_id="d_1", reason_code="quota_headroom")


def test_reflex_without_action_refused(tmp_path):
    p = PolicyRuntime(tmp_path / "p.yaml")
    with pytest.raises(PolicyError, match="action"):
        p.author({"match": {}}, decision_id="d_1", reason_code="quota_headroom")


def test_roundtrip_through_yaml_preserves_provenance(tmp_path):
    path = tmp_path / "p.yaml"
    p = PolicyRuntime(path)
    p.author({"match": {"event_type": "worker_done"}, "action": {"lane": "W1"}, "ttl_days": 30},
             decision_id="d_00412", reason_code="quota_peak_multiplier")
    p.save()
    reloaded = PolicyRuntime(path)
    assert len(reloaded.reflexes) == 1
    assert reloaded.reflexes[0].created_by == "d_00412"


def test_peak_window_matching():
    # 06:30Z == 02:30 America/New_York -> inside the GLM peak window.
    assert _in_window("02:00-06:00 America/New_York", "2026-08-06T06:30:00Z")
    assert not _in_window("02:00-06:00 America/New_York", "2026-08-06T18:00:00Z")
    assert _in_window("22:00-02:00 UTC", "2026-08-06T23:00:00Z"), "windows may wrap midnight"
    assert not _in_window("02:00-06:00 Not/AZone", "2026-08-06T06:30:00Z"), "bad zone fails closed"


def test_unparseable_provenance_is_treated_as_expired(tmp_path):
    from kernel.policy_runtime import Reflex
    r = Reflex({}, {"lane": "W1"}, "executive", "x", "d_1", 30, created_ts="not-a-date")
    assert r.expired(dt.datetime.now(dt.UTC)), "fail closed on unreadable provenance"


def test_non_reflexable_events_never_match(tmp_path):
    from executive.ledger import Ledger
    p = PolicyRuntime(tmp_path / "p.yaml")
    p.author({"match": {}, "action": {"lane": "W1"}, "ttl_days": 30},
             decision_id="d_1", reason_code="quota_headroom")
    for t in (EventType.HUMAN_INPUT, EventType.BUDGET_THRESHOLD, EventType.DECISION_INVALID):
        assert p.match(Event("t", 0, "2026-08-06T06:30:00Z", t, {}), Ledger()) is None


# ========================================================== B5 verification ====
def test_v2_refuses_to_run_before_b0():
    """V2 is not a default. It exists because B0 measured it beating self-review."""
    with pytest.raises(B0NotRun, match="binding gate"):
        require_b0_pass()


def test_v2_prompt_is_blinded():
    prompt = build_prompt("do the thing", "diff --git a/x b/x", "pytest: ok")
    for leak in ("byte-identical", "self-review", "cross-review", "operator commentary"):
        assert leak not in prompt, f"{leak!r} leaked into the reviewer's prompt"
    assert "do the thing" in prompt and "{{OBJECTIVE}}" not in prompt


def test_findings_parsed_from_fenced_json():
    text = 'Reasoning first.\n```json\n{"findings":[{"file":"a.py","line":1}],"verdict":"reject"}\n```'
    assert parse_findings(text)["verdict"] == "reject"


def test_unparseable_review_is_flagged_not_treated_as_clean():
    """Silently reading garbage as 'no findings' would bias catch rates downward."""
    out = parse_findings("I have no idea what you want.")
    assert out["verdict"] == "unparseable" and out["_parse_error"]


def test_diff_scope_catches_deleted_test():
    diff = "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n-def test_thing():\n-    assert f() == 1\n"
    r = diff_scope(diff, ["**"])
    assert not r["pass"] and r["test_weakening"]


def test_diff_scope_catches_skip_marker():
    diff = "+++ b/tests/test_x.py\n+@pytest.mark.skip(reason=\"flaky\")\n def test_thing():\n"
    assert diff_scope(diff, ["**"])["test_weakening"]


def test_diff_scope_catches_out_of_scope_edit():
    diff = "+++ b/src/ok.py\n+x=1\n+++ b/infra/secrets.tf\n+y=2\n"
    r = diff_scope(diff, ["src/**"])
    assert "infra/secrets.tf" in r["out_of_scope"] and not r["pass"]


def test_clean_diff_passes_scope():
    assert diff_scope("+++ b/src/ok.py\n+x = 1\n", ["src/**"])["pass"]


def test_v3_sampling_is_deterministic_and_five_percent():
    a = [sampled(f"c_{i:04d}") for i in range(2000)]
    b = [sampled(f"c_{i:04d}") for i in range(2000)]
    assert a == b, "two runs must select the same sample or the audit is not reproducible"
    assert 0.03 < sum(a) / 2000 < 0.07


def test_v3_always_checks_escalations_and_gates():
    sc = SpotChecker({})
    assert sc.should_check("c_9999", escalation=True)
    assert sc.should_check("c_9999", promotion_gate=True)


# ============================================================== B6 logging ====
def test_decision_rows_are_written_and_groupable(conn):
    audit, log = AuditLog(conn), DecisionLog(conn)
    ev = Event("t1", 0, "2026-08-06T12:00:00Z", EventType.TASK_RECEIVED, {})
    audit.append(ev)
    eid = log.event_id_for("t1", 0)
    for verb, reason in (("dispatch", "lane_capability_fit"), ("dispatch", "cost_ceiling"),
                         ("retry", "verify_failed_revise")):
        log.record(event_id=eid, ledger_hash="abc123",
                   decision=Decision(verb, reason, 0.9, {}), model="luna", cost_usd=0.002)
    assert log.count() == 3
    assert log.verb_counts() == {"dispatch": 2, "retry": 1}
    assert {r["reason_code"] for r in log.reason_code_outcomes()} == {
        "lane_capability_fit", "cost_ceiling", "verify_failed_revise"}


def test_outcomes_are_labels_for_decisions(conn):
    log = DecisionLog(conn)
    conn.execute("INSERT INTO contracts (id, task_id, fleet, spec_json, status, created_ts)"
                 " VALUES ('c_0001','t1','WR','{}','done','2026-08-06')")
    log.record_outcome("c_0001", {"v1_pass": True, "cost_usd": 0.01, "score": 0.9})
    row = conn.execute("SELECT * FROM outcomes WHERE contract_id='c_0001'").fetchone()
    assert row["v1_pass"] == 1 and row["score"] == 0.9


def test_shadow_never_lets_the_candidate_act():
    from executive.client import ScriptedExecutive
    real = ScriptedExecutive([{"schema_version": 1, "decision": "dispatch",
                               "reason_code": "lane_capability_fit", "confidence": 0.9,
                               "params": {"lane": "WR"}}])
    shadow = ScriptedExecutive([{"schema_version": 1, "decision": "halt",
                                 "reason_code": "terminal_failure", "confidence": 0.9,
                                 "params": {"status": "failed"}}])
    d = ShadowExecutive(real, shadow).decide("ledger", {"type": "task_received"})
    assert d.decision == "dispatch", "the executive of record must win, always"


def test_shadow_survives_a_broken_candidate():
    from executive.client import ScriptedExecutive

    class Broken:
        name = "broken"
        def decide(self, *_a): raise RuntimeError("model died")

    real = ScriptedExecutive([{"schema_version": 1, "decision": "halt",
                               "reason_code": "terminal_success", "confidence": 1.0,
                               "params": {"status": "success"}}])
    sh = ShadowExecutive(real, Broken())
    assert sh.decide("l", {"type": "x"}).decision == "halt"
    assert sh.agreement.disagreements[0]["error"].startswith("RuntimeError")


def test_dashboard_renders_from_an_empty_db(tmp_path):
    from evals.dashboard import build
    html = build(tmp_path / "empty.db")
    assert "<title>Chingis" in html and "no data yet" in html
    assert "M2 prerequisite" in html


# ================================================================== loop ====
def test_test_deleting_trajectory_is_disqualified():
    s = score_outcome({"v1_pass": True, "v2_verdict": "approve", "cost_usd": 0.0,
                       "v1_detail": {"checks": [{"check": "diff_scope",
                                                 "test_weakening": ["deleted"]}]}})
    assert s.disqualified and s.value == 0.0


def test_cheapness_cannot_outrank_correctness():
    cheap_wrong = score_outcome({"v1_pass": False, "v2_verdict": "reject", "cost_usd": 0.0})
    dear_right = score_outcome({"v1_pass": True, "v2_verdict": "approve", "cost_usd": 0.9})
    assert dear_right.value > cheap_wrong.value


def test_advantage_filter_drops_the_weak_and_the_disqualified():
    rows = [({"id": i}, score_outcome({"v1_pass": i > 2, "v2_verdict": "approve",
                                       "cost_usd": 0.1})) for i in range(6)]
    rows.append(({"id": 99}, score_outcome(
        {"v1_pass": True, "v1_detail": {"checks": [{"check": "diff_scope",
                                                    "test_weakening": ["x"]}]}})))
    kept = advantage_filter(rows)
    assert {r["id"] for r in kept} <= {3, 4, 5}
    assert 99 not in {r["id"] for r in kept}


def test_promotion_gate_discards_on_any_failure():
    a = Agreement(routine_total=400, routine_agreed=395)
    ok = check_promotion(agreement=a, seeded_catch_rate=0.8, b0_catch_rate=0.75,
                         eval_delta=0.02, write_set_touched=["policy/routing_policy.yaml"])
    assert ok.promoted

    regressed = check_promotion(agreement=a, seeded_catch_rate=0.8, b0_catch_rate=0.75,
                                eval_delta=-0.01, write_set_touched=[])
    assert not regressed.promoted and "discarded, not patched live" in str(regressed)


def test_promotion_blocked_by_a_single_gate_divergence():
    a = Agreement(routine_total=400, routine_agreed=400)
    a.gate_divergences.append({"of_record": "request_human", "shadow": "dispatch"})
    r = check_promotion(agreement=a, seeded_catch_rate=0.9, b0_catch_rate=0.75,
                        eval_delta=0.1, write_set_touched=[])
    assert not r.promoted, "perfect agreement plus one gate divergence must still be discarded"


def test_promotion_blocked_by_seeded_defect_regression():
    """B5's bar: seeded defects caught at the B0-measured rate, NOT below."""
    a = Agreement(routine_total=400, routine_agreed=400)
    r = check_promotion(agreement=a, seeded_catch_rate=0.60, b0_catch_rate=0.75,
                        eval_delta=0.5, write_set_touched=[])
    assert not r.promoted


def test_loop_cannot_write_outside_its_write_set():
    """The gate never learns."""
    a = Agreement(routine_total=400, routine_agreed=400)
    for illegal in ("kernel/meters.py", "evals/heldout/x.manifest", "verify/seeded/manifest.json"):
        r = check_promotion(agreement=a, seeded_catch_rate=0.9, b0_catch_rate=0.75,
                            eval_delta=0.1, write_set_touched=[illegal])
        assert not r.promoted, f"{illegal} must not be writable by the loop"
