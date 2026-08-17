#!/usr/bin/env python3
"""Live end-to-end smoke on the W3 (dsh) lane. Spends real metered money -- not pytest.

The unit tests prove the adapter's command line, environment, containment, and failure
normalization against a fake `dsh`. They cannot prove that a real packaged harness,
driven headless, produces a real diff that V1 then judges and that the SCREEN keeps its
prose away from the executive. That is what this does:

    uv run python tests/live_w3_smoke.py /path/to/fixture/repo

Route it at another lab by exporting W3_MODEL="anthropic/claude-sonnet-4-5" (with the
matching key). Changing labs is a string here; that is the point of the lane.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.dsh_adapter import DshAdapter
from executive.client import ScriptedExecutive
from kernel.audit import AuditLog
from kernel.capabilities import Registry
from kernel.db import connect, migrate
from kernel.meters import Meters
from kernel.replay import replay_task
from kernel.runtime import Runtime
from verify.v1_runners import V1Runner

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/livefix")
MODEL = os.environ.get("W3_MODEL", "zai/glm-5.2")

OBJECTIVE = (
    "The test suite has one failing test: median() returns the wrong value for an "
    "even-length list. Fix src/stats.py so median() averages the two middle values for "
    "even-length input. Do not modify any test."
)


def main() -> int:
    registry = Registry()
    provider, model = MODEL.split("/", 1)
    w3 = DshAdapter(provider=provider, model=model)
    ok, msg = w3.healthcheck()
    print(f"W3 healthcheck: {ok} -- {msg}")
    if not ok:
        return 1

    conn = connect(":memory:")
    migrate(conn)

    budget = {"wall_min": 6, "quota_units": 0, "tokens_usd": 0.25,
              "tool_calls": 20, "inlane_retries": 0}
    params = {"lane": "W3", "contract_id": "c_0001", "objective": OBJECTIVE,
              "model": MODEL, "context_refs": ["src/**"],
              "capabilities": ["fs:worktree", "net:none", "proc:bash"],
              "budget": budget, "repo": str(REPO), "base_ref": "main",
              "verification": ["V1:pytest"]}

    executive = ScriptedExecutive([
        {"schema_version": 1, "decision": "dispatch", "reason_code": "lane_capability_fit",
         "confidence": 0.9, "params": params},
        {"schema_version": 1, "decision": "halt", "reason_code": "terminal_success",
         "confidence": 0.95, "params": {"status": "success"}},
    ])

    rt = Runtime("t_live_w3", executive, {"W3": w3}, audit=AuditLog(conn),
                 meters=Meters(usd=0.50, quota_units=10), registry=registry,
                 workdir=Path(".worktrees"))
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
        print(f"  {c.get('check')}: pass={c.get('pass')} {str(c.get('tail', ''))[-160:]}")
    print(f"  V1 overall: {'PASS' if v1.get('pass') else 'FAIL'}")

    if done:
        w = done[0].payload.get("worker", {})
        print("\n=== what the executive was allowed to see (spec §7) ===")
        print(f"  summary : {w.get('summary')}")
        print(f"  flags   : {w.get('flags')}  screened_by={w.get('screened_by')}")

    print(f"\n=== budget ===\n  {rt.meters.snapshot()}")
    print(f"\n=== replay ===\n  {replay_task(conn, 't_live_w3')}")
    return 0 if done else 2


if __name__ == "__main__":
    raise SystemExit(main())
