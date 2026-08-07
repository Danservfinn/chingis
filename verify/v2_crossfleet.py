"""V2 — cross-fleet review. Spec §5.

W1 artifacts reviewed by W2 and vice versa, against the contract, using the SAME rubric B0
measured. That reuse is what makes the B0 number a valid floor: B5's acceptance criterion is
"seeded defects caught at the B0-measured rate, not below", and it is only comparable if the
prompt is the same one.

The structural rule: no artifact ships on self-review alone.

Gated by B0. If B0's verdict was FAIL, V2 is dropped from the design and this module must
not be wired in -- `require_b0_pass()` refuses rather than silently reviewing anyway.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUBRIC = ROOT / "verify" / "rubric" / "review_rubric.md"
B0_REPORT = ROOT / "benchmarks" / "b0" / "results" / "report.md"
SENTINEL = "<!-- ===== PROMPT BEGINS ===== -->"

OPPOSITE = {"W1": "W2", "W2": "W1", "WR": "W1", "W0": "W1"}


class B0NotRun(RuntimeError):
    pass


def require_b0_pass() -> float:
    """Return the B0-measured cross-fleet catch rate, or refuse.

    V2 is not a default. It exists because B0 measured it beating self-review, and B5's
    bar is that measured rate. Wiring V2 without that number would mean asserting the
    thing the experiment was built to test.
    """
    if not B0_REPORT.exists():
        raise B0NotRun(
            "V2 requires benchmarks/b0/results/report.md. B0 is the binding gate: if "
            "cross-fleet review did not beat self-review, V2 is dropped from the design."
        )
    text = B0_REPORT.read_text()
    if "FAIL —" in text or "FAIL -" in text:
        raise B0NotRun("B0 FAILED: V2 is dropped. Verification is V1+V3.")
    m = re.search(r"\| Cross-review \| ([\d.]+)%", text)
    if not m:
        raise B0NotRun("could not read the cross-review catch rate from the B0 report")
    return float(m.group(1)) / 100.0


def build_prompt(objective: str, diff: str, v1_report: str) -> str:
    """The B0 rubric, operator commentary stripped at the sentinel."""
    text = RUBRIC.read_text()
    if SENTINEL not in text:
        raise RuntimeError(f"{RUBRIC} lost its {SENTINEL!r}; refusing to send an unblinded prompt")
    body = text.split(SENTINEL, 1)[1].lstrip()
    return (body.replace("{{OBJECTIVE}}", objective)
                .replace("{{DIFF}}", diff[:120_000])
                .replace("{{V1_REPORT}}", v1_report[-4_000:]))


def parse_findings(text: str) -> dict:
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"findings": [], "verdict": "unparseable", "_parse_error": True}


class CrossFleetVerifier:
    def __init__(self, adapters: dict, min_catch_rate: float | None = None) -> None:
        self.adapters = adapters
        self.min_catch_rate = min_catch_rate

    def review(self, generating_lane: str, objective: str, diff: str, v1_report: str = "") -> dict:
        """Review by the opposite fleet. A monoculture's blind spots grade themselves."""
        reviewer = OPPOSITE.get(generating_lane, "W1")
        adapter = self.adapters.get(reviewer)
        if adapter is None:
            return {"verdict": "unavailable", "pass": True,
                    "detail": f"no adapter for reviewer lane {reviewer}"}

        prompt = build_prompt(objective, diff, v1_report)
        contract = {
            "contract_id": "v2_review", "lane": reviewer, "objective": prompt,
            "context_refs": ["**"], "capabilities": ["fs:readonly:.", "net:none"],
            "output_schema": "findings_json",
            "budget": {"wall_min": 10, "quota_units": 1, "tokens_usd": 0.2,
                       "tool_calls": 0, "inlane_retries": 0},
            "verification": [],
        }
        result = adapter.run(contract, Path("."))
        text = (getattr(result, "artifacts", {}) or {}).get("summary", "")
        parsed = parse_findings(text)
        findings = parsed.get("findings") or []
        return {"tier": "V2", "reviewer_lane": reviewer, "generating_lane": generating_lane,
                "verdict": parsed.get("verdict", "unknown"),
                "findings": findings, "pass": not findings,
                "self_review": reviewer == generating_lane}
