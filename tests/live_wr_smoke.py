#!/usr/bin/env python3
"""Live end-to-end smoke on the WR lane. Spends real (small) money -- not part of pytest.

This is the difference between "the adapters pass against test doubles" and "a real model
drove the kernel-gated tool loop, inside a real worktree, and produced a real diff that V1
then judged". Run it deliberately:

    uv run python tests/live_wr_smoke.py /path/to/fixture/repo
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.base import Worktree
from adapters.raw_adapter import RawAdapter
from executive.client import ScriptedExecutive
from kernel.audit import AuditLog
from kernel.capabilities import Registry
from kernel.db import connect, migrate
from kernel.meters import Meters
from kernel.replay import replay_task
from kernel.runtime import Runtime
from verify.v1_runners import V1Runner, diff_scope

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/livefix")
MODEL = os.environ.get("WR_MODEL", "glm-4.6")

OBJECTIVE = (
    "The test suite has one failing test: median() returns the wrong value for an "
    "even-length list. Fix src/stats.py so median() averages the two middle values for "
    "even-length input. Do not modify any test."
)


def main() -> int:
    registry = Registry()
    wr = RawAdapter(registry)
    ok, msg = wr.healthcheck()
    print(f"WR healthcheck: {ok} -- {msg}")
    if not ok:
        return 1

    conn = connect(":memory:")
    migrate(conn)

    contract = {
        "contract_id": "c_0001", "lane": "WR", "model": MODEL,
        "repo": str(REPO), "base_ref": "main",
        "objective": OBJECTIVE,
        "context_refs": ["src/**"],
        "capabilities": ["fs:worktree", "net:none", "proc:bash"],
        "output_schema": "diff+summary",
        "budget": {"wall_min": 5, "quota_units": 0, "tokens_usd": 0.25,
                   "tool_calls": 20, "inlane_retries": 0},
        "verification": ["V1:pytest"],
    }

    executive = ScriptedExecutive([
        {"schema_version": 1, "decision": "dispatch", "reason_code": "lane_capability_fit",
         "confidence": 0.9, "params": {"lane": "WR", "contract_id": "c_0001",
                                       "objective": OBJECTIVE, "model": MODEL,
                                       "context_refs": ["src/**"],
                                       "capabilities": ["fs:worktree", "net:none", "proc:bash"],
                                       "budget": contract["budget"], "repo": str(REPO),
                                       "base_ref": "main",
                                       "verification": ["V1:pytest"]}},
        {"schema_version": 1, "decision": "halt", "reason_code": "terminal_success",
         "confidence": 0.95, "params": {"status": "success"}},
    ])

    workdir = Path(".worktrees")
    rt = Runtime("t_live", executive, {"WR": wr}, audit=AuditLog(conn),
                 meters=Meters(usd=0.50, quota_units=10), registry=registry,
                 workdir=workdir)

    rt.verifier = V1Runner()
    rt.submit(OBJECTIVE)
    asyncio.run(rt.run())

    print("\n=== events ===")
    for e in rt.bus.processed:
        print(f"  seq={e.seq} {e.type}")

    done = [e for e in rt.bus.processed if str(e.type) in ("worker_done", "verify_failed")]
    v1 = (done[0].payload.get("v1") if done else {}) or {"checks": []}

    print("\n=== V1 (kernel-run, inside the worktree, before any model sees it) ===")
    for c in v1.get("checks", []):
        print(f"  {c.get('check')}: pass={c.get('pass')} {str(c.get('tail',''))[-160:]}")
    print(f"  V1 overall: {'PASS' if v1.get('pass') else 'FAIL'}")

    for c in v1.get("checks", []):
        if c.get("check") == "diff_scope":
            print(f"\n=== diff scope ===\n  touched={c['files_touched']}")
            print(f"  out_of_scope={c['out_of_scope']}  test_weakening={c['test_weakening']}")

    if done:
        w = done[0].payload.get("worker", {})
        print(f"\n=== what the executive was allowed to see (spec §7) ===")
        print(f"  summary : {w.get('summary')}")
        print(f"  flags   : {w.get('flags')}  screened_by={w.get('screened_by')}")

    print(f"\n=== budget ===\n  {rt.meters.snapshot()}")
    r = replay_task(conn, "t_live")
    print(f"\n=== replay ===\n  {r}")
    return 0 if done else 2


if __name__ == "__main__":
    raise SystemExit(main())
