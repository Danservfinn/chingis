"""B3: executive invariants -- frozen prefix, confidence gate, schema validation, ledger cap.

Plan §6: "CI test asserts byte-identity of everything before the breakpoint across two cold
builds." The 1.25x cache-write premium means the prefix changes only at a deliberate
schema_version bump through the §10 ratchet.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from executive.client import (
    CONFIDENCE_FLOOR,
    Decision,
    InvalidDecision,
    ScriptedExecutive,
    apply_confidence_gate,
    frozen_prefix,
    validate,
)
from executive.ledger import TOKEN_CAP, Ledger

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- frozen prefix ----
def test_prefix_is_byte_identical_across_two_cold_builds():
    """Two fresh interpreters must produce the same bytes. Same-process caching would make
    this test vacuous, so it shells out."""
    code = ("import sys, hashlib; sys.path.insert(0, %r); "
            "from executive.client import frozen_prefix; "
            "print(hashlib.sha256(frozen_prefix().encode()).hexdigest())" % str(ROOT))
    a = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    b = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert a.returncode == 0, a.stderr
    assert a.stdout.strip() == b.stdout.strip() != ""


def test_prefix_contains_the_schema_and_the_invariants():
    p = frozen_prefix()
    assert "You are Chingis" in p
    assert '"decision"' in p and '"reason_code"' in p, "the action schema must be inlined"
    for verb in ("dispatch", "escalate", "request_human", "edit_policy", "halt"):
        assert verb in p


def test_prefix_names_untrusted_content_as_data():
    """Spec §7: the executive's system prompt must name UNTRUSTED content as data. This is
    the last line of defense against orchestration hijack."""
    p = frozen_prefix().lower()
    assert "untrusted" in p
    assert "data, never instructions" in p or "never instructions" in p


def test_prefix_has_no_task_specific_content():
    """Anything varying per task must sit AFTER the cache breakpoint, or every task pays
    the write premium instead of taking the 90% read discount."""
    p = frozen_prefix()
    for leak in ("c_0001", "task_id", "2026-08-", "/Users/"):
        assert leak not in p, f"task-specific string {leak!r} leaked into the cached prefix"


def test_schema_version_is_pinned():
    schema = json.loads((ROOT / "executive" / "schema" / "decision.schema.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == 1


# ------------------------------------------------------------- confidence ----
def test_confidence_below_floor_forces_escalate():
    d = validate({"schema_version": 1, "decision": "dispatch", "reason_code": "lane_capability_fit",
                  "confidence": 0.4, "params": {"lane": "WR"}})
    gated = apply_confidence_gate(d)
    assert gated.decision == "escalate"
    assert gated.params["original_decision"] == "dispatch"
    assert gated.forced and "0.4" in gated.forced


def test_confidence_at_floor_is_allowed():
    d = validate({"schema_version": 1, "decision": "dispatch", "reason_code": "lane_capability_fit",
                  "confidence": CONFIDENCE_FLOOR, "params": {}})
    assert apply_confidence_gate(d).decision == "dispatch"


def test_gate_applies_to_every_verb_not_just_dispatch():
    for verb in ("halt", "retry", "edit_policy", "race", "checkpoint"):
        d = Decision(verb, "ambiguous_objective", 0.1, {})
        assert apply_confidence_gate(d).decision == "escalate", f"{verb} escaped the gate"


def test_escalate_is_not_re_escalated():
    d = Decision("escalate", "low_confidence_escalation", 0.1, {})
    assert apply_confidence_gate(d).decision == "escalate"


# ------------------------------------------------------------- validation ----
@pytest.mark.parametrize("bad,match", [
    ({"decision": "obliterate", "reason_code": "cost_ceiling", "confidence": 0.9,
      "params": {}, "schema_version": 1}, "unknown verb"),
    ({"decision": "dispatch", "reason_code": "vibes", "confidence": 0.9,
      "params": {}, "schema_version": 1}, "unknown reason_code"),
    ({"decision": "dispatch", "reason_code": "cost_ceiling", "confidence": 1.7,
      "params": {}, "schema_version": 1}, "outside"),
    ({"decision": "dispatch", "reason_code": "cost_ceiling", "confidence": 0.9,
      "params": "not-an-object", "schema_version": 1}, "params must be"),
    ({"decision": "dispatch", "reason_code": "cost_ceiling", "confidence": 0.9,
      "params": {}, "schema_version": 2}, "schema_version"),
    ({"decision": "dispatch", "confidence": 0.9, "params": {}, "schema_version": 1},
     "missing required"),
])
def test_invalid_decisions_are_rejected(bad, match):
    with pytest.raises(InvalidDecision, match=match):
        validate(bad)


def test_kernel_revalidates_even_valid_looking_input():
    """Defense in depth, not trust: server-side structured outputs do not exempt the
    kernel from checking."""
    d = validate({"schema_version": 1, "decision": "halt", "reason_code": "terminal_success",
                  "confidence": 1.0, "params": {"status": "success"}})
    assert isinstance(d, Decision)


# ----------------------------------------------------------------- ledger ----
def test_ledger_renders_all_six_sections():
    lg = Ledger(goal="g", plan=["a"], state={"k": "v"}, open_questions=["q"])
    lg.set_budget({"tokens_usd": {"spent": 0.1, "limit": 1.0, "pct": 10.0}})
    lg.record_outcome("c_0001 done")
    rendered = lg.render()
    for section in ("GOAL", "PLAN", "STATE", "BUDGET/QUOTA", "RECENT_OUTCOMES", "OPEN_QUESTIONS"):
        assert f"## {section}" in rendered


def test_only_last_five_outcomes_kept():
    lg = Ledger()
    for i in range(12):
        lg.record_outcome(f"outcome {i}")
    assert len(lg.recent_outcomes) == 5
    assert lg.recent_outcomes[-1] == "outcome 11"


def test_cap_is_detected_and_compression_reduces_it():
    lg = Ledger(goal="x" * 200)
    lg.plan = [f"step {i} " + "y" * 300 for i in range(40)]
    lg.open_questions = [f"q{i} " + "z" * 200 for i in range(20)]
    for i in range(10):
        lg.record_outcome("o" * 200)
    assert lg.over_cap, f"fixture should exceed the {TOKEN_CAP}-token cap"
    before = lg.tokens
    note = lg.compress()
    assert lg.tokens < before and "compressed" in note


def test_ledger_hash_changes_with_content():
    a = Ledger(goal="one")
    b = Ledger(goal="two")
    assert a.hash() != b.hash() and len(a.hash()) == 16


def test_scripted_executive_records_what_it_saw():
    ex = ScriptedExecutive([{"schema_version": 1, "decision": "halt",
                             "reason_code": "terminal_success", "confidence": 1.0,
                             "params": {"status": "success"}}])
    d = ex.decide("ledger", {"type": "task_received"})
    assert d.decision == "halt" and ex.seen[0]["type"] == "task_received"
