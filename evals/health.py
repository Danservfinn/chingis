#!/usr/bin/env python3
"""Instruments that watch the instruments. Spec §5, §11; plan B5/B6.

Three failure modes the design names, each with a documented threshold and — until now —
nothing measuring it:

1. **Verifier collapse** (§5, §11). "If V2 approval rates drift above ~98% for weeks, the
   reviewers have gone soft or the prompts have converged." A verifier that approves
   everything is indistinguishable from no verifier, and it fails silently: throughput looks
   great right up until the artifacts are wrong.

2. **Seeded-defect regression** (B5). "Seeded defects caught at the B0-measured rate, not
   below." `CrossFleetVerifier` accepted a `min_catch_rate` and never used it, so the floor
   B0 exists to establish had nothing enforcing it afterwards.

3. **No captive workload** (§11). "Contract volume is an M1 prerequisite, tracked weekly."
   The premortem's own answer to "nothing feeds the loop" was to track this, and a count in
   a status command is not a trend.

Every check here reports rather than acts. These are gauges; the operator decides.

    ./evals/health.py            # all three, against chingis.db
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.db import connect, migrate  # noqa: E402

#: §5's number. Above this, sustained, the reviewers are not reviewing.
COLLAPSE_THRESHOLD = 0.98
#: §12 M1 wants >=100 held-out contracts; weekly volume is the leading indicator.
M1_WEEKLY_TARGET = 10
B0_REPORT = ROOT / "benchmarks" / "b0" / "results" / "report.md"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    value: float | None = None

    def __str__(self) -> str:
        v = f"  [{self.value:.1%}]" if self.value is not None else ""
        return f"{'ok  ' if self.ok else 'WARN'} {self.name}{v}: {self.detail}"


def b0_floor() -> float | None:
    """The catch rate B0 measured. None if B0 has not reported."""
    if not B0_REPORT.exists():
        return None
    import re
    m = re.search(r"\| Cross-review \| ([\d.]+)%", B0_REPORT.read_text())
    return float(m.group(1)) / 100.0 if m else None


def verifier_collapse(conn, min_samples: int = 20) -> Check:
    rows = list(conn.execute(
        "SELECT v2_verdict FROM outcomes WHERE v2_verdict IS NOT NULL AND v2_verdict != ''"))
    n = len(rows)
    if n < min_samples:
        return Check("verifier_collapse", True,
                     f"only {n}/{min_samples} V2 verdicts logged; too early to read")
    approvals = sum(1 for r in rows if str(r["v2_verdict"]).lower() in ("approve", "pass"))
    rate = approvals / n
    if rate > COLLAPSE_THRESHOLD:
        return Check("verifier_collapse", False,
                     f"V2 approved {approvals}/{n}. Above {COLLAPSE_THRESHOLD:.0%} the "
                     "reviewers have gone soft or the prompts have converged -- re-run the "
                     "seeded suite and rotate the reviewer prompt before trusting a verdict",
                     rate)
    return Check("verifier_collapse", True, f"V2 approved {approvals}/{n}, below the "
                 f"{COLLAPSE_THRESHOLD:.0%} drift line", rate)


def seeded_catch_regression(conn, measured: float | None = None) -> Check:
    """B5's floor, enforced rather than merely documented."""
    floor = b0_floor()
    if floor is None:
        return Check("seeded_catch_floor", True,
                     "no B0 report yet, so there is no floor to hold V2 to. V2 should not "
                     "be wired until there is")
    if measured is None:
        return Check("seeded_catch_floor", True,
                     f"B0 floor is {floor:.1%}; no post-B0 seeded run to compare against yet")
    if measured < floor:
        return Check("seeded_catch_floor", False,
                     f"seeded catch {measured:.1%} is BELOW the B0-measured floor "
                     f"{floor:.1%}. B5's criterion is 'not below' -- the verifier has "
                     "regressed since it was calibrated", measured)
    return Check("seeded_catch_floor", True,
                 f"seeded catch {measured:.1%} holds the {floor:.1%} floor", measured)


def contract_volume(conn, weeks: int = 4) -> Check:
    """§11's 'no captive workload' gauge. A count is not a trend."""
    rows = list(conn.execute(
        "SELECT strftime('%Y-%W', created_ts) wk, COUNT(*) n FROM contracts "
        "GROUP BY wk ORDER BY wk DESC LIMIT ?", (weeks,)))
    if not rows:
        return Check("contract_volume", False,
                     "zero contracts logged. Nothing feeds the loop, which is the premortem "
                     "failure mode the benchmark suite was supposed to answer")
    recent = rows[0]["n"]
    trend = ", ".join(f"{r['wk']}:{r['n']}" for r in rows)
    if recent < M1_WEEKLY_TARGET:
        return Check("contract_volume", False,
                     f"{recent} contracts this week, below the {M1_WEEKLY_TARGET}/wk that "
                     f"reaches M1's 100 held-out in a quarter. Trend: {trend}")
    return Check("contract_volume", True, f"{recent} this week. Trend: {trend}")


def run_all(db: str | Path = ROOT / "chingis.db", seeded_measured: float | None = None) -> list[Check]:
    conn = connect(db)
    migrate(conn)
    try:
        return [verifier_collapse(conn),
                seeded_catch_regression(conn, seeded_measured),
                contract_volume(conn)]
    finally:
        conn.close()


def main() -> int:
    checks = run_all()
    print("== harness health ==")
    for c in checks:
        print(f"  {c}")
    warned = [c for c in checks if not c.ok]
    print(f"\n{len(checks) - len(warned)} ok, {len(warned)} warn")
    print("These are gauges, not gates: nothing here halts a run. They exist so that "
          "'the verifier went soft' and 'the loop is starving' are visible as numbers rather "
          "than discovered as surprises.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
