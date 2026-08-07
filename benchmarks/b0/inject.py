#!/usr/bin/env python3
"""Deterministic post-generation defect injection for B0.

The defect is never requested of the generating model — asking a model to write a bug
produces a *conspicuous* bug, which measures nothing. It is applied here, from the
write-protected manifest, after generation and before review.

Exit 0 = injected and verified. Exit 1 = injection did not land; the caller marks the
pair `injection_failed` and the scorer excludes it from the catch-rate denominator,
loudly. Silently dropping failed injections is the exact dishonesty the seeded corpus
exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Where to record ground truth. MUST be outside the worktree: anything "
        "written inside it lands in the diff and un-blinds the reviewer.",
    )
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text())
    entry = next(
        (c for c in manifest["contracts"] if c["contract_id"] == args.contract), None
    )
    if entry is None:
        print(f"inject: {args.contract} absent from manifest", file=sys.stderr)
        return 1
    if not entry.get("seeded"):
        return 0

    inj = entry.get("injection", {})
    method = inj.get("method")

    if method == "manual":
        print(
            f"inject: {args.contract} is class '{entry['defect_class']}', which is not "
            "mechanisable. The operator must apply it by hand and record the location "
            "in the manifest before this contract can be scored.",
            file=sys.stderr,
        )
        return 1

    if method != "python_sub":
        print(f"inject: unknown method {method!r}", file=sys.stderr)
        return 1

    target = args.worktree / inj["file"]
    if not target.exists():
        print(f"inject: target {target} does not exist in the worktree", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8", errors="replace")

    # Precondition: the shape the injection assumes must actually be there. A generator
    # that solved the objective a different way invalidates this pair -- that is a real
    # outcome, not an error to paper over.
    if pre := inj.get("precondition"):
        if not re.search(pre, text):
            print(
                f"inject: precondition /{pre}/ not found in {inj['file']} — "
                "the generator did not produce the shape this injection assumes",
                file=sys.stderr,
            )
            return 1

    new_text, n = re.subn(
        inj["pattern"], inj["replacement"], text, count=inj.get("count", 1)
    )
    if n == 0:
        print(f"inject: pattern /{inj['pattern']}/ matched nothing", file=sys.stderr)
        return 1
    if new_text == text:
        print("inject: substitution was a no-op", file=sys.stderr)
        return 1

    target.write_text(new_text, encoding="utf-8")

    # Record where it actually landed. Ground truth is measured, not assumed.
    line_no = next(
        (
            i
            for i, (a, b) in enumerate(
                zip(text.splitlines(), new_text.splitlines()), start=1
            )
            if a != b
        ),
        1,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "injection.json").write_text(
        json.dumps(
            {
                "contract_id": args.contract,
                "defect_class": entry["defect_class"],
                "file": inj["file"],
                "line": line_no,
                "substitutions": n,
            },
            indent=2,
        )
    )
    print(f"inject: {args.contract} {entry['defect_class']} -> {inj['file']}:{line_no}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
