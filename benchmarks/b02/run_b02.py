#!/usr/bin/env python3
"""B0.2 — reviewer identity vs lineage crossing. Runs the factorial over B0's artifacts.

    ./benchmarks/b02/run_b02.py --dry-run          # what would run, spends nothing
    ./benchmarks/b02/run_b02.py --reviewers anthropic
    ./benchmarks/b02/run_b02.py                    # all three identities

No generation phase: B0's artifacts survive on disk (20 contracts x 2 generators), and the
pre-registration requires this corpus and this rubric so catch rates stay comparable to a
measured baseline. Regenerating would forfeit that comparability for no gain.

The §4.1 false-flag repair turned out not to be a corpus change at all: the corpus is
already **10 seeded : 10 clean**. B0 simply never reviewed the clean half, which is why
its report says the guardrail "cannot be evaluated until it has data". This runner reviews
both halves, so every reviewer gets both a catch rate and a false-flag rate.

Matching is imported from `score_b0` rather than reimplemented. A second copy of "did this
finding hit the seeded defect" would let B0 and B0.2 drift into scoring the same review
differently, and the whole point of reusing the corpus is that the numbers compare.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks" / "b0"))

from benchmarks.b0.score_b0 import matches_defect            # noqa: E402
from verify.reviewers import anthropic_oauth, zai_anthropic_endpoint  # noqa: E402
from verify.v2_crossfleet import build_prompt, parse_findings  # noqa: E402

ARTIFACTS = ROOT / "benchmarks" / "b0" / "artifacts"
MANIFEST = ROOT / "verify" / "seeded" / "manifest.json"
OUT = Path(__file__).resolve().parent / "results"

#: Reviewer identity -> callable. Two z.ai identities and one Anthropic: the within-lab
#: pair is what separates IDENTITY from LINEAGE, which two lanes could never do.
REVIEWERS = {
    "glm-5.3": lambda p: zai_anthropic_endpoint(p, model="glm-5.3"),
    "glm-4.7": lambda p: zai_anthropic_endpoint(p, model="glm-4.7"),
    "anthropic": anthropic_oauth,
}
#: Which lab each reviewer identity belongs to, for the lineage contrast.
REVIEWER_LAB = {"glm-5.3": "zai", "glm-4.7": "zai", "anthropic": "anthropic"}
#: Which lab generated each artifact. B0's W1 was Anthropic, W2 was z.ai (DEVIATIONS D1).
GENERATOR_LAB = {"W1": "anthropic", "W2": "zai"}


def load_artifacts() -> list[dict]:
    m = json.loads(MANIFEST.read_text())
    truth = {c["contract_id"]: c for c in m["contracts"]}
    out = []
    for patch in sorted(ARTIFACTS.glob("*/W*/diff.patch")):
        cid, gen = patch.parent.parent.name, patch.parent.name
        diff = patch.read_text(errors="replace")
        if not diff.strip() or cid not in truth:
            continue
        prompt_file = patch.parent / "contract_prompt.md"
        out.append({
            "contract_id": cid, "generator": gen, "diff": diff,
            "objective": (prompt_file.read_text(errors="replace")[:2000]
                          if prompt_file.exists() else cid),
            "truth": truth[cid], "seeded": bool(truth[cid].get("seeded")),
        })
    return out


def review(item: dict, name: str, tol: int) -> dict:
    """One review. Returns the row that scoring reads."""
    prompt = build_prompt(item["objective"], item["diff"], "V1 ran; see the diff.")
    row = {"contract_id": item["contract_id"], "generator": item["generator"],
           "reviewer": name, "seeded": item["seeded"],
           "crossed": GENERATOR_LAB.get(item["generator"]) != REVIEWER_LAB[name]}
    try:
        parsed = parse_findings(REVIEWERS[name](prompt))
    except Exception as e:                                    # noqa: BLE001
        return {**row, "status": "transport_error", "detail": type(e).__name__}
    if parsed.get("_parse_error") or parsed.get("verdict") == "unparseable":
        # Never scored as "found nothing": B0 did exactly that with 5 of 24 replies and
        # biased its own catch rate downward by an unknown amount.
        return {**row, "status": "unparseable", "findings": 0}
    findings = parsed.get("findings") or []
    row["findings"] = len(findings)
    if item["seeded"]:
        inj = item["truth"].get("injection", {}) or {}
        t = {"file": inj.get("file", ""), "defect_class": item["truth"].get("defect_class"),
             "line": inj.get("line")}
        verdicts = [matches_defect(f, t, tol) for f in findings]
        row["status"] = ("hit" if "hit" in verdicts
                         else "ambiguous" if "ambiguous" in verdicts else "miss")
    else:
        # A clean artifact: every finding is a false flag. This is the denominator B0
        # never had, and without it a reviewer that rejects everything scores perfectly.
        row["status"] = "false_flag" if findings else "clean_pass"
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewers", default=",".join(REVIEWERS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0,
                    help="skip the first N artifacts, so a long run can resume")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default="rows")
    args = ap.parse_args()

    names = [n.strip() for n in args.reviewers.split(",") if n.strip()]
    items = load_artifacts()[args.start:]
    if args.limit:
        items = items[:args.limit]
    tol = json.loads(MANIFEST.read_text()).get("line_tolerance", 3)

    seeded_n = sum(1 for i in items if i["seeded"])
    print(f"artifacts: {len(items)} ({seeded_n} seeded, {len(items) - seeded_n} clean)")
    print(f"reviewers: {names}")
    print(f"reviews:   {len(items) * len(names)}")
    if args.dry_run:
        print("\n--dry-run: nothing spent.")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{args.tag}.json"
    # Merge, never overwrite: a long run is executed in batches, and a batch that clobbers
    # its predecessors turns a resumable run into a restart.
    rows: list[dict] = json.loads(dest.read_text()) if dest.exists() else []
    for name in names:
        for i, item in enumerate(items, 1):
            r = review(item, name, tol)
            rows.append(r)
            dest.write_text(json.dumps(rows, indent=2))   # durable per review
            print(f"  [{name}] {item['contract_id']}/{item['generator']:<3} "
                  f"{r['status']:<14} ({i}/{len(items)})", flush=True)
        dest.write_text(json.dumps(rows, indent=2))
    report(rows)
    return 0


def report(rows: list[dict]) -> None:
    print("\n=== B0.2 — reviewer identity vs lineage ===\n")
    print(f"{'reviewer':<12} {'catch':>12} {'false-flag':>12} {'unparseable':>12}")
    print("-" * 52)
    for name in dict.fromkeys(r["reviewer"] for r in rows):
        rs = [r for r in rows if r["reviewer"] == name]
        seeded = [r for r in rs if r["seeded"] and r["status"] in ("hit", "miss", "ambiguous")]
        clean = [r for r in rs if not r["seeded"] and r["status"] in ("false_flag", "clean_pass")]
        hits = sum(1 for r in seeded if r["status"] == "hit")
        ff = sum(1 for r in clean if r["status"] == "false_flag")
        unp = sum(1 for r in rs if r["status"] == "unparseable")
        print(f"{name:<12} {f'{hits}/{len(seeded)}':>12} {f'{ff}/{len(clean)}':>12} {unp:>12}")

    print("\nby lineage (the factor B0 measured):")
    for crossed in (False, True):
        rs = [r for r in rows if r.get("crossed") is crossed and r["seeded"]
              and r["status"] in ("hit", "miss", "ambiguous")]
        hits = sum(1 for r in rs if r["status"] == "hit")
        label = "cross-lab" if crossed else "same-lab"
        print(f"  {label:<10} {hits}/{len(rs)}")
    print("\nThresholds are frozen in PREREGISTRATION.md §5. This runner REPORTS; it does "
          "not adjudicate,\nand n here is far below the derived 103 -- read the interval, "
          "not the point estimate.")


if __name__ == "__main__":
    raise SystemExit(main())
