#!/usr/bin/env python3
"""Monthly held-out rotation. Plan §6.

    "Monthly, operator moves ~20% of benchmark contracts into evals/heldout/, logs the
     manifest hash. The loop never trains on anything whose hash appears there."

Operator-only, and deliberately so: `evals/` is a protected path, so running this produces
changes that cannot be committed without CHINGIS_OPERATOR=1. The loop can read the
resulting manifest (loop/consolidate.py does) but can never write one.

Why this exists as a tool rather than a habit: M4 tests whether improvement is general or
memorized, and its answer is only worth anything if the held-out set was genuinely withheld.
A rotation done by hand, occasionally, with no hash log, cannot support that claim -- you
would have no way to prove after the fact which contracts the loop had never seen.

    ./ops/rotate_heldout.py --plan          # show what would move, change nothing
    ./ops/rotate_heldout.py --commit        # perform the rotation
    ./ops/rotate_heldout.py --verify        # re-check every logged hash still matches
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmarks" / "b0" / "contracts"
HELDOUT = ROOT / "evals" / "heldout"
FRACTION = 0.20


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def manifest_path(stamp: str) -> Path:
    return HELDOUT / f"{stamp}.manifest"


def already_held() -> set[str]:
    out: set[str] = set()
    for m in HELDOUT.glob("*.manifest"):
        for line in m.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 2:
                    out.add(parts[1])
    return out


def choose(stamp: str) -> list[Path]:
    """Deterministic selection, seeded by the rotation date.

    Seeded rather than random so a rotation is reproducible from its own manifest -- if
    someone later asks "why these twenty percent", the answer is checkable rather than
    "the shuffle came out that way".
    """
    held = already_held()
    pool = sorted(p for p in BENCH.glob("c_*.json") if p.name not in held)
    if not pool:
        return []
    k = max(1, round(len(pool) * FRACTION))
    rng = random.Random(f"heldout:{stamp}")
    return sorted(rng.sample(pool, min(k, len(pool))))


def rotate(stamp: str, commit: bool) -> int:
    HELDOUT.mkdir(parents=True, exist_ok=True)
    picked = choose(stamp)
    if not picked:
        print("nothing left to rotate: every benchmark contract is already held out.")
        return 0

    lines = [
        f"# Held-out rotation {stamp}",
        f"# {len(picked)} contract(s), ~{FRACTION:.0%} of the un-held pool.",
        "# The loop never trains on anything whose hash appears here. Format: <sha256:16> <name>",
    ]
    for p in picked:
        lines.append(f"{file_hash(p)} {p.name}")

    print(f"rotation {stamp}: {len(picked)} contract(s)")
    for p in picked:
        print(f"  {file_hash(p)}  {p.name}")

    if not commit:
        print("\n--plan only; nothing moved. Re-run with --commit to perform the rotation.")
        return 0

    dest = HELDOUT / "contracts"
    dest.mkdir(exist_ok=True)
    for p in picked:
        shutil.move(str(p), str(dest / p.name))
    manifest_path(stamp).write_text("\n".join(lines) + "\n")
    print(f"\nmoved {len(picked)} contract(s) -> {dest}")
    print(f"wrote {manifest_path(stamp)}")
    print("\nThis touched evals/, a protected path. Commit with:")
    print(f'  CHINGIS_OPERATOR=1 git commit -m "operator: held-out rotation {stamp}"')
    return 0


def verify() -> int:
    """Every logged hash must still match the file it names. A held-out contract that has
    been edited since it was withheld is no longer held out."""
    dest = HELDOUT / "contracts"
    problems: list[str] = []
    checked = 0
    for m in sorted(HELDOUT.glob("*.manifest")):
        for line in m.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            want, name = line.split()[:2]
            path = dest / name
            checked += 1
            if not path.exists():
                problems.append(f"{m.name}: {name} is missing from {dest}")
            elif file_hash(path) != want:
                problems.append(
                    f"{m.name}: {name} changed since it was withheld "
                    f"({want} -> {file_hash(path)}). It is no longer held out."
                )
    if problems:
        print(f"FAIL: {len(problems)} problem(s) across {checked} held-out contract(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"ok: {checked} held-out contract(s), every hash intact")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="show what would move")
    g.add_argument("--commit", action="store_true", help="perform the rotation")
    g.add_argument("--verify", action="store_true", help="re-check logged hashes")
    ap.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m"),
                    help="rotation stamp, default this month (YYYY-MM)")
    args = ap.parse_args()

    if args.verify:
        return verify()
    return rotate(args.date, commit=args.commit)


if __name__ == "__main__":
    sys.exit(main())
