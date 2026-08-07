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


def _git_show(worktree: Path, rel: str) -> str | None:
    """Contents of a file at HEAD, or None if it did not exist there."""
    import subprocess
    p = subprocess.run(["git", "-C", str(worktree), "show", f"HEAD:{rel}"],
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def _record(args, entry: dict, file: str, line: int) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "injection.json").write_text(json.dumps(
        {"contract_id": args.contract, "defect_class": entry["defect_class"],
         "file": file, "line": line, "substitutions": 1}, indent=2))


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

    if method == "move_change":
        # edit_wrong_file: apply the worker's change to a near-twin module instead, and
        # restore the intended one. Not expressible as a regex, but entirely deterministic.
        src = args.worktree / inj["file"]
        twin = args.worktree / inj["twin"]
        if not src.exists():
            print(f"inject: {src} absent", file=sys.stderr)
            return 1
        base = _git_show(args.worktree, inj["file"])
        twin.parent.mkdir(parents=True, exist_ok=True)
        twin.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        if base is None:
            src.unlink()
        else:
            src.write_text(base, encoding="utf-8")
        _record(args, entry, inj["twin"], 1)
        print(f"inject: {args.contract} edit_wrong_file -> {inj['twin']}")
        return 0

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

    # Some defects need more than one edit to stay behaviourally identical. Turning a set
    # into a list is only invisible to V1 if `.add` becomes `.append` too -- otherwise the
    # injection raises AttributeError and a V1-INVISIBLE class becomes V1-visible, which
    # measures the opposite of what the corpus is for.
    subs = inj.get("subs") or [{"pattern": inj["pattern"],
                                "replacement": inj["replacement"],
                                "count": inj.get("count", 1)}]
    new_text, n = text, 0
    for s in subs:
        new_text, k = re.subn(s["pattern"], s["replacement"], new_text,
                              count=s.get("count", 1))
        n += k
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
