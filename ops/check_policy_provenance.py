#!/usr/bin/env python3
"""CI provenance check for the adaptive plane's routing policy.

Every reflex must be executive-authored, traceable to the decision that created it,
and mortal. Developer-authored routing content is a CI failure. (Spec §6, §11
"Rigidity regression")

Deliberately dependency-free: this guard runs in a git hook, so it cannot rely on
the project venv being present or correct. It uses a strict line grammar covering
exactly the shape routing_policy.yaml is allowed to take, and FAILS CLOSED on
anything it cannot confidently parse. A guard that guesses is not a guard.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = ("author", "reason_code", "created_by", "ttl_days")
DECISION_ID = re.compile(r"^d_\d{5}$")
ENTRY_START = re.compile(r"^- (\w+):\s*(.*)$")
ENTRY_CONT = re.compile(r"^  (\w+):\s*(.*)$")


def parse_reflexes(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Return (entries, hard_errors). Fails closed: unparseable => error."""
    entries: list[dict[str, str]] = []
    errors: list[str] = []
    in_block = False
    current: dict[str, str] | None = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
        if not line.strip():
            continue

        if line.rstrip() in ("reflexes:", "reflexes: []"):
            in_block = True
            if line.rstrip() == "reflexes: []":
                in_block = False
            continue

        if not in_block:
            # Top-level scalars outside the reflex block (e.g. schema_version).
            if re.match(r"^\w+:\s*\S*$", line):
                continue
            errors.append(f"line {lineno}: unparseable top-level content: {line!r}")
            continue

        if m := ENTRY_START.match(line):
            if current:
                entries.append(current)
            current = {m.group(1): m.group(2).strip()}
        elif m := ENTRY_CONT.match(line):
            if current is None:
                errors.append(f"line {lineno}: continuation before any list entry")
            else:
                current[m.group(1)] = m.group(2).strip()
        else:
            errors.append(f"line {lineno}: does not match the permitted grammar: {line!r}")

    if current:
        entries.append(current)
    return entries, errors


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "policy/routing_policy.yaml")
    if not path.exists():
        print(f"FAIL: {path} is missing. The adaptive plane must have a policy file.")
        return 1

    entries, errors = parse_reflexes(path.read_text())

    for i, e in enumerate(entries):
        where = f"{path}: reflex #{i + 1}"
        for key in REQUIRED:
            if key not in e:
                errors.append(f"{where}: missing required provenance field {key!r}")
        if e.get("author") != "executive":
            errors.append(
                f"{where}: author is {e.get('author')!r}, must be 'executive'. "
                "Developer-authored routing is a CI failure -- zero developer orchestration."
            )
        if (cb := e.get("created_by")) and not DECISION_ID.match(cb):
            errors.append(f"{where}: created_by {cb!r} is not a decision id (d_NNNNN)")
        if (ttl := e.get("ttl_days")) is not None:
            if not ttl.isdigit() or int(ttl) <= 0:
                errors.append(
                    f"{where}: ttl_days {ttl!r} must be a positive integer. "
                    "Reflexes decay by default; an immortal reflex is developer rigidity wearing a costume."
                )

    if errors:
        print(f"FAIL: routing policy provenance check ({len(errors)} problem(s))")
        for err in errors:
            print(f"  - {err}")
        return 1

    n = len(entries)
    print(f"ok: routing policy provenance ({n} reflex{'' if n == 1 else 'es'}, all executive-authored with TTL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
