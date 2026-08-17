#!/usr/bin/env python3
"""M0 — the control arm. Plan §5 (B6), spec §12.

    "B0 bash harness formalized as the permanent M0 control arm."

M0 is the founding claim under test: *an adaptive harness beats a deliberately dumb shell
at doing tasks.* The dumb shell is not a strawman to be beaten — it is the thing Chingis
must actually outperform, so it is frozen here and never improved.

    Fixed routing. Fixed retry. No executive. No policy. No verification beyond V1.

Pre-registered in spec §12: Chingis is expected to LOSE the routine mix. That loss is
consistent with the founding post, not against it. The claim is about the *variable* mix,
over >=60 contracts, on success-per-dollar.

    ./benchmarks/m0_control.py --contracts benchmarks/b0/contracts --lane W1
    ./benchmarks/m0_control.py --compare chingis.db     # M0 vs Chingis on logged outcomes
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base import Worktree                      # noqa: E402
from adapters.dsh_adapter import DshAdapter             # noqa: E402
from adapters.raw_adapter import RawAdapter             # noqa: E402
from kernel.capabilities import Registry                # noqa: E402
from verify.v1_runners import V1Runner                  # noqa: E402

#: The control arm's entire policy. Frozen on purpose -- every knob here is one Chingis has
#: and M0 does not, and the comparison is only meaningful while these stay put.
# Was W1 (Claude Code) until those lanes were removed 2026-08-17. M0's POLICY
# (fixed routing, fixed retry, no executive) is unchanged; only which fixed lane
# it routes to moved, because the old one no longer exists.
FIXED_LANE = "WR"
FIXED_RETRIES = 1
FIXED_BUDGET = {"wall_min": 8, "quota_units": 1, "tokens_usd": 0.50,
                "tool_calls": 20, "inlane_retries": 0}


@dataclass
class M0Result:
    contract_id: str
    status: str
    v1_pass: bool
    attempts: int
    usd: float
    tokens_in: int
    tokens_out: int
    wall_s: float
    files_touched: list[str] = field(default_factory=list)
    test_weakening: list[str] = field(default_factory=list)


def run_one(contract: dict, adapter, registry: Registry, workdir: Path) -> M0Result:
    """Dispatch, and on failure retry exactly once. No judgment anywhere in this function."""
    cid = contract["contract_id"]
    # The lane the adapter actually IS, not the module default: --lane exists so the
    # control arm can be run on another lane, and stamping FIXED_LANE into every spec
    # labelled a W2 or W3 run as W1 in its own recorded artifact.
    spec = {**contract, "lane": getattr(adapter, "lane", FIXED_LANE),
            "capabilities": ["fs:worktree", "net:none", "proc:bash"],
            "output_schema": "diff+summary", "budget": FIXED_BUDGET,
            "verification": ["V1:pytest"]}
    started, usd, attempts = time.time(), 0.0, 0
    tok_in = tok_out = 0
    v1: dict = {}
    result = None

    for attempt in range(1, FIXED_RETRIES + 2):
        attempts = attempt
        wt = workdir / f"m0_{cid}_{attempt}"
        try:
            with Worktree(contract["repo"], contract.get("base_ref", "HEAD"), wt):
                result = adapter.run(spec, wt)
                rc = result.raw_cost or {}
                usd += float(rc.get("usd") or 0.0)
                tok_in += int(rc.get("tokens_in") or 0)
                tok_out += int(rc.get("tokens_out") or 0)
                v1 = V1Runner().run_v1(spec, result, wt)
        except Exception as e:  # noqa: BLE001
            v1 = {"pass": False, "checks": [{"check": "harness", "pass": False, "detail": str(e)}]}
            continue
        if v1.get("pass"):
            break

    scope = next((c for c in v1.get("checks", []) if c.get("check") == "diff_scope"), {})
    return M0Result(
        contract_id=cid,
        status=str(getattr(result, "status", "failed")),
        v1_pass=bool(v1.get("pass")),
        attempts=attempts,
        usd=round(usd, 6),
        tokens_in=tok_in,
        tokens_out=tok_out,
        wall_s=round(time.time() - started, 1),
        files_touched=scope.get("files_touched", []),
        test_weakening=scope.get("test_weakening", []),
    )


def success_per_dollar(results: list[M0Result]) -> dict:
    """The M0 metric. A success that weakened a test is not a success.

    Without that exclusion the control arm could 'win' by deleting failing tests, which is
    the exact behaviour the seeded corpus exists to detect -- and the comparison would then
    reward the thing the whole design is built to prevent.
    """
    honest = [r for r in results if r.v1_pass and not r.test_weakening]
    spend = sum(r.usd for r in results)
    mtok = sum(r.tokens_in + r.tokens_out for r in results) / 1e6
    return {
        "contracts": len(results),
        "v1_passed": sum(1 for r in results if r.v1_pass),
        "honest_successes": len(honest),
        "disqualified_for_test_weakening": sum(1 for r in results if r.test_weakening),
        "total_usd": round(spend, 4) if spend else None,
        # None, not 0.0. The endpoint reports tokens, not dollars, and no price table is
        # configured -- so dollars are UNKNOWN, which is not the same claim as free. A
        # zero here would divide into an infinite success-per-dollar and make an unpriced
        # arm look perfect, which is how a comparison invents its own winner.
        "success_per_dollar": round(len(honest) / spend, 2) if spend else None,
        "total_mtok": round(mtok, 4),
        "successes_per_mtok": round(len(honest) / mtok, 2) if mtok else None,
        "mean_wall_s": round(sum(r.wall_s for r in results) / len(results), 1) if results else 0,
    }


def compare(db_path: str) -> int:
    """M0 versus Chingis on logged outcomes. Reports, never adjudicates."""
    from kernel.db import connect, migrate

    conn = connect(db_path)
    migrate(conn)
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(v1_pass) passed, SUM(cost_usd) usd FROM outcomes").fetchone()
    conn.close()

    baseline_path = ROOT / "benchmarks" / "m0_baseline.json"
    if not baseline_path.exists():
        print("no M0 baseline yet. Run the control arm first:")
        print("  ./benchmarks/m0_control.py --contracts benchmarks/b0/contracts")
        return 1
    m0 = json.loads(baseline_path.read_text())["summary"]

    n = row["n"] or 0
    chingis_spd = ((row["passed"] or 0) / row["usd"]) if row["usd"] else None
    print("== M0 control arm ==")
    print(f"  {m0['honest_successes']}/{m0['contracts']} honest successes, "
          f"${m0['total_usd']}, {m0['success_per_dollar']} per dollar")
    print("== Chingis ==")
    print(f"  {row['passed'] or 0}/{n} V1-passed, ${round(row['usd'] or 0, 4)}, "
          f"{round(chingis_spd, 2) if chingis_spd else 'n/a'} per dollar")
    if n < 60:
        print(f"\nM0's pre-registered bar needs >=60 contracts on the VARIABLE mix; "
              f"Chingis has {n}. Too early to read.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contracts", type=Path)
    ap.add_argument("--lane", default=FIXED_LANE, choices=["WR", "W3"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--compare", metavar="DB")
    args = ap.parse_args()

    if args.compare:
        return compare(args.compare)
    if not args.contracts:
        ap.error("--contracts or --compare is required")

    registry = Registry()
    adapter = {"WR": lambda: RawAdapter(registry), "W3": DshAdapter}[args.lane]()

    files = sorted(args.contracts.glob("c_*.json"))
    if args.limit:
        files = files[: args.limit]

    results: list[M0Result] = []
    for f in files:
        contract = json.loads(f.read_text())
        print(f"[M0] {contract['contract_id']} -> {args.lane}", flush=True)
        r = run_one(contract, adapter, registry, ROOT / ".worktrees")
        results.append(r)
        print(f"      V1={'pass' if r.v1_pass else 'fail'} attempts={r.attempts} "
              f"${r.usd} {r.wall_s}s", flush=True)

    summary = success_per_dollar(results)
    out = ROOT / "benchmarks" / "m0_baseline.json"
    out.write_text(json.dumps(
        {"lane": args.lane, "fixed_retries": FIXED_RETRIES, "budget": FIXED_BUDGET,
         "summary": summary, "results": [asdict(r) for r in results]}, indent=2) + "\n")
    print(f"\n{json.dumps(summary, indent=2)}\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
