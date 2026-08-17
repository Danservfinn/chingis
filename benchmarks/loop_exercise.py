#!/usr/bin/env python3
"""Drive the M-ladder loop end to end on whatever the database already holds.

    ./benchmarks/loop_exercise.py [--db chingis.db]

The loop -- scoring, V3 sampling, dataset building, the promotion gate, reflex
authoring, and the proposal ratchet -- was `scaffolded, unexercised` for the whole life
of the project. Every component of this system that had never been RUN turned out to
contain a defect that would have silently produced a wrong answer, so this exists to run
the remaining half cheaply, at small n, before anything is scaled to a 60-contract corpus
where the same defects would cost hours instead of seconds.

It mutates nothing except `outcomes.score` and its own scratch ratchet file. It is a
diagnostic, not a promotion: `check_promotion` is CALLED, and whatever it says is
reported rather than acted on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from executive.shadow import Agreement                     # noqa: E402
from kernel.db import connect, migrate                     # noqa: E402
from loop.consolidate import build_dataset                 # noqa: E402
from loop.promote import check_promotion                   # noqa: E402
from loop.ratchet import GateProbe, Ratchet                # noqa: E402
from loop.score import advantage_filter, score_outcome     # noqa: E402
from verify.v3_spotcheck import SpotChecker, off_family_for  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "chingis.db"))
    args = ap.parse_args()
    conn = connect(args.db)
    migrate(conn)
    findings: list[str] = []

    # ------------------------------------------------------------- 1. score --
    print("== 1. scoring outcomes ==")
    rows = [dict(r) for r in conn.execute(
        "SELECT o.contract_id, o.v1_pass, o.v1_detail_json, o.v2_verdict, o.v3_verdict,"
        " o.cost_usd, c.fleet FROM outcomes o LEFT JOIN contracts c ON c.id = o.contract_id")]
    if not rows:
        print("  no outcomes. Run ./cli.py submit first.")
        return 1
    scored = []
    for r in rows:
        r["v1_detail"] = json.loads(r.get("v1_detail_json") or "{}")
        s = score_outcome(r)
        scored.append((r, s))
        conn.execute("UPDATE outcomes SET score = ? WHERE contract_id = ?",
                     (s.value, r["contract_id"]))
        print(f"  {r['contract_id']:<18} score={s.value:<7} {s.components}")
    conn.commit()

    uniq = {s.value for _, s in scored}
    if len(uniq) == 1 and len(scored) > 1:
        findings.append(
            f"every outcome scored identically ({uniq.pop()}). With V2 dropped and V3 "
            "unwired, score = 0.5*v1 + 0.25*0.5 + 0.15*0.5 + 0.10*efficiency, "
            "and every surviving term is a constant -- so the score carries exactly one "
            "bit (did V1 pass) while presenting as a continuous ranking. Advantage "
            "filtering on a constant cannot rank anything, so consolidation would select "
            "its training examples arbitrarily.")

    # ------------------------------------------------------- 2. V3 sampling --
    print("\n== 2. V3 spot-check sampling ==")
    sc = SpotChecker({})
    sampled = [r["contract_id"] for r, _ in scored if sc.should_check(r["contract_id"])]
    print(f"  {len(sampled)}/{len(scored)} sampled at {sc.rate:.0%}: {sampled or '(none)'}")
    for r, _ in scored:
        lane = r.get("fleet") or "WR"
        try:
            fam = off_family_for(lane)
        except ValueError as e:
            findings.append(f"V3 cannot review lane {lane}: {e}")
            continue
        v = sc.check(r["contract_id"], lane, "objective", "diff")
        if v.get("verdict") == "not_wired":
            findings.append(
                f"V3 returns verdict='not_wired' for {lane} (reviewer family {fam!r}): the "
                "sampling logic is live but no reviewer is attached, so V3 contributes a "
                "neutral 0.5 to every score and verifies nothing.")
            break

    # -------------------------------------------------------- 3. consolidate --
    print("\n== 3. advantage filter + dataset ==")
    kept = advantage_filter(scored)
    print(f"  advantage_filter kept {len(kept)}/{len(scored)}")
    try:
        ds = build_dataset(conn)
        print(f"  build_dataset -> {len(ds)} examples")
        if not ds:
            findings.append("build_dataset produced 0 examples: there is nothing to "
                            "consolidate from, so the M-ladder cannot take a step.")
    except Exception as e:                                   # noqa: BLE001
        findings.append(f"build_dataset RAISED: {type(e).__name__}: {e}")
        print(f"  build_dataset RAISED {type(e).__name__}: {e}")

    # ------------------------------------------------------------ 4. gate --
    print("\n== 4. promotion gate ==")
    try:
        g = check_promotion(agreement=Agreement(0, 0, 0.0), seeded_catch_rate=0.0,
                            b0_catch_rate=0.0, eval_delta=0.0, write_set_touched=[])
        print(f"  {g}")
    except Exception as e:                                   # noqa: BLE001
        findings.append(f"check_promotion RAISED: {type(e).__name__}: {e}")
        print(f"  RAISED {type(e).__name__}: {e}")

    # ---------------------------------------------------------- 5. ratchet --
    print("\n== 5. ratchet ==")
    scratch = ROOT / ".worktrees" / "loop_exercise_staging.yaml"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.unlink(missing_ok=True)
    r = Ratchet(scratch)
    p = r.propose("tool", "summarize_diff", "recurring manual step in review",
                  proposed_by="loop_exercise", targets=["adapters/tools/summarize.py"])
    print(f"  ordinary proposal -> {p.status}")
    try:
        r.propose("tool", "widen_screen", "screen too strict", proposed_by="loop_exercise",
                  targets=["kernel/screen.py"])
        findings.append("the ratchet ACCEPTED a proposal targeting kernel/ -- the gate is open")
    except GateProbe:
        print(f"  gate probe -> refused and counted ({len(r.gate_probes)})")
    r.save()

    # ------------------------------------------------------------- report --
    print("\n" + "=" * 70)
    if findings:
        print(f"{len(findings)} FINDING(S):\n")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. {f}\n")
    else:
        print("no findings.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
