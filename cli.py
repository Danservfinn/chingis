#!/usr/bin/env python3
"""Chingis — the operator surface. Spec §1 (OP).

    ./cli.py health                       what is reachable right now
    ./cli.py submit "objective" --repo P  run one task
    ./cli.py status                       what the log knows
    ./cli.py dashboard                    regenerate the static eval board

Task submission, escalation terminal, promotion decisions. Everything else the system does
to itself; this is the part you do to it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adapters.dsh_adapter import DshAdapter                  # noqa: E402
from adapters.local_adapter import LocalAdapter             # noqa: E402
from adapters.raw_adapter import RawAdapter                 # noqa: E402
from contracts.store import ContractStore                   # noqa: E402
from executive.client import LunaClient, ScriptedExecutive  # noqa: E402
from executive.ledger import Ledger                         # noqa: E402
from kernel.audit import AuditLog                           # noqa: E402
from kernel.capabilities import Registry                    # noqa: E402
from kernel.db import connect, migrate                      # noqa: E402
from kernel.decision_log import DecisionLog                 # noqa: E402
from kernel.meters import Meters                            # noqa: E402
from kernel.policy_runtime import PolicyRuntime             # noqa: E402
from kernel.replay import replay_task                       # noqa: E402
from kernel.runtime import Runtime                          # noqa: E402
from kernel.screen import Screener                          # noqa: E402
from verify.v1_runners import V1Runner                      # noqa: E402


#: One switch for every local-model consumer. Default OFF: W0 is opt-in, so nothing
#: reaches for a local server unless the operator asked for it. Set CHINGIS_W0=on (or
#: 1/true/yes) to re-enable both the W0 lane and the screener's semantic layer.
def w0_enabled() -> bool:
    return os.environ.get("CHINGIS_W0", "off").strip().lower() in ("on", "1", "true", "yes")


def build_spotchecker():
    """V3 with a real reviewer when one is reachable, else the tier stays dark.

    Nothing ever constructed a SpotChecker in the live path, so the tier's 5% sampling
    sampled nothing and every outcome scored a neutral 0.5 for V3 -- one of the two
    reasons the score carried a single live bit.
    """
    from verify.reviewers import anthropic_oauth
    from verify.v3_spotcheck import SpotChecker
    import shutil
    if not shutil.which(os.environ.get("CLAUDE_BIN", "claude")):
        return None
    # Rate is overridable so the tier can be exercised deliberately. The DEFAULT stays
    # 5%: V3 is metered attention, and a tier that reviews everything is V2 by another
    # name -- which B0 already rejected.
    rate = float(os.environ.get("V3_SAMPLE_RATE", SpotChecker({}).rate))
    return SpotChecker({}, rate=rate, reviewer=anthropic_oauth)


def build_screener(w0_factory=None) -> Screener:
    """Build the screener WITH its semantic layer when a local model is reachable.

    `cmd_submit` used to call `Screener()` with no argument, so layer 3 could never run
    -- not because W0 was offline, but because it was never handed over. Every live run
    in the system's history reported `screened_by=heuristic`, which read as "no server"
    and was actually "not wired". A down lane masked a wiring gap.

    Failure here costs the semantic layer and nothing else. The structural and heuristic
    layers do not depend on W0, which is the design's own claim: screening degrades, it
    never disappears.
    """
    if w0_factory is None and not w0_enabled():
        # Disabled: the screen keeps its structural and heuristic layers, which is the
        # posture it held for the project's whole life. Screening degrades; it never
        # disappears.
        return Screener(None)
    factory = w0_factory or (lambda: LocalAdapter(model=os.environ.get("W0_MODEL", "qwen3:0.6b")))
    try:
        w0 = factory()
        ok, _ = w0.healthcheck() if hasattr(w0, "healthcheck") else (True, "")
        return Screener(w0 if ok else None)
    except Exception:                                   # noqa: BLE001
        return Screener(None)


def build_adapters(registry: Registry) -> dict:
    """The lane set. Two workers and a local summarizer.

    The Claude Code lanes (W1 native Anthropic, W2 -> Z.ai) were removed 2026-08-17 at
    operator direction. Both drove the same third-party CLI, whose inner shell Chingis
    could neither see nor pin, and whose model on W2 was a mapping z.ai owned rather than
    one we selected -- W2 had been labelled glm-5.2 for months while actually serving
    glm-4.7. What replaces them is the same reach with fewer moving parts: W3 routes to
    Anthropic, xAI, and DeepSeek by changing one string, given a key.

    Consequence carried deliberately: B0's numbers were generated on W1/W2 and remain
    valid history, but they can no longer be reproduced on this machine.
    """
    lanes = {"WR": RawAdapter(registry), "W3": DshAdapter()}
    if w0_enabled():
        lanes["W0"] = LocalAdapter()
    return lanes


#: Terminal per-contract events. A contract may produce several (a retry emits one each);
#: the LAST is its outcome, because otherwise a lane that retries often would look more
#: productive rather than less.
TERMINAL_EVENTS = ("worker_done", "verify_failed", "worker_failed", "worker_refused")


def record_outcomes(conn, events, task_id: str) -> int:
    """Write one `outcomes` row per contract. Returns how many contracts were recorded.

    This existed as `DecisionLog.record_outcome` and was called from nowhere, so the
    table stayed empty and `m0_control.py --compare` always read an empty Chingis arm --
    which is why M0 vs Chingis, the founding comparison, had never been run end to end.

    Cost is recorded as NULL when the lane does not report dollars. WR meters tokens and
    knows its spend; W3's provider does not expose per-call cost. Writing 0.0 for the
    unknown case would give an unpriced lane an infinite success-per-dollar and hand
    whichever arm used it a win it did not earn.
    """
    from contracts.store import ContractStore
    from kernel.decision_log import DecisionLog
    log = DecisionLog(conn)
    latest: dict[str, dict] = {}
    for e in events:
        if str(getattr(e, "type", "")) not in TERMINAL_EVENTS:
            continue
        p = getattr(e, "payload", None) or {}
        cid = p.get("contract_id")
        if not cid:
            continue
        cost = p.get("cost") or {}
        v1 = p.get("v1") or p.get("detail") or {}
        v3 = p.get("v3") or {}
        latest[ContractStore.row_id(task_id, cid)] = {
            "v3_verdict": v3.get("verdict"),
            "v1_pass": bool(v1.get("pass")),
            "v1_detail": v1,
            # .get, never `or 0.0`: absent means unknown, and unknown is not free.
            "cost_usd": cost.get("usd"),
            "quota_units": cost.get("quota_units"),
            "wall_s": p.get("wall_s"),
        }
    for cid, outcome in latest.items():
        log.record_outcome(cid, outcome)
    conn.commit()
    return len(latest)


def cmd_health(args) -> int:
    registry = Registry()
    print("== lanes ==")
    for name, ad in build_adapters(registry).items():
        ok, msg = ad.healthcheck()
        print(f"  {'ok  ' if ok else 'DOWN'} {name}: {msg[:110]}")

    print("== executive ==")
    ex = LunaClient(transport=args.transport, model=args.model)
    ok, msg = ex.healthcheck()
    print(f"  {'ok  ' if ok else 'DOWN'} {ex.name}/{ex.model} via {args.transport}: {msg[:110]}")
    return 0


def cmd_submit(args) -> int:
    conn = connect(args.db)
    migrate(conn)
    registry = Registry()
    adapters = build_adapters(registry)

    executive = (ScriptedExecutive() if args.dry_run
                 else LunaClient(transport=args.transport, model=args.model))

    ledger = Ledger(goal=args.objective)
    ledger.state["task_type"] = args.task_type

    task_id = args.task_id or f"t_{DecisionLog(conn).count():05d}"
    rt = Runtime(
        task_id, executive, adapters,
        audit=AuditLog(conn), meters=Meters(usd=args.usd, quota_units=args.quota),
        registry=registry, ledger=ledger,
        policy=PolicyRuntime(), verifier=V1Runner(), screener=build_screener(),
        spotchecker=build_spotchecker(),
        contract_store=ContractStore(conn), workdir=ROOT / ".worktrees",
    )

    # The repo the worktree is cut from travels on the contract, not in global config:
    # one task, one blast radius.
    if args.repo:
        ledger.state["repo"] = str(Path(args.repo).resolve())

    print(f"task {task_id}: {args.objective}")
    rt.submit(args.objective)
    asyncio.run(rt.run(max_events=args.max_events))

    log = DecisionLog(conn)
    for i, d in enumerate(rt.state.decisions):
        eid = log.event_id_for(task_id, min(i, len(rt.bus.processed) - 1))
        if eid:
            log.record(event_id=eid, ledger_hash=rt.ledger.hash(), decision=d,
                       model=getattr(executive, "model", executive.name),
                       latency_ms=rt.decision_latencies[i] if i < len(rt.decision_latencies) else None)

    recorded = record_outcomes(conn, rt.bus.processed, task_id)

    print("\n== events ==")
    for e in rt.bus.processed:
        print(f"  seq={e.seq:<3} {e.type}")
    print("\n== decisions ==")
    for d in rt.state.decisions:
        forced = f"  [FORCED: {d.forced}]" if d.forced else ""
        print(f"  {d.decision:<16} {d.reason_code:<28} conf={d.confidence}{forced}")
    if rt.reflex_hits:
        print(f"\n== reflexes fired: {len(rt.reflex_hits)} ==")
    if rt.state.awaiting_human:
        print(f"\n== AWAITING OPERATOR ==\n  {rt.state.awaiting_human}")
    print(f"\n== budget ==\n  {rt.meters.snapshot()}")
    print(f"== halt ==\n  {rt.bus.halt_reason or '(queue drained)'}")
    print(f"== outcomes ==\n  {recorded} contract(s) recorded")
    print(f"== replay ==\n  {replay_task(conn, task_id)}")
    conn.close()
    return 0


def cmd_status(args) -> int:
    conn = connect(args.db)
    migrate(conn)
    q = lambda s: list(conn.execute(s))
    print(f"decisions : {DecisionLog(conn).count()}   (M2 needs >=1000)")
    print(f"events    : {q('SELECT COUNT(*) n FROM events')[0]['n']}")
    print(f"contracts : {q('SELECT COUNT(*) n FROM contracts')[0]['n']}")
    print("\nverbs:")
    for r in q("SELECT verb, COUNT(*) n FROM decisions GROUP BY verb ORDER BY n DESC"):
        print(f"  {r['verb']:<18} {r['n']}")
    print("\nreason codes:")
    for r in q("SELECT reason_code, COUNT(*) n FROM decisions GROUP BY reason_code ORDER BY n DESC LIMIT 10"):
        print(f"  {r['reason_code']:<30} {r['n']}")
    print("\ntasks:")
    for r in q("SELECT task_id, COUNT(*) n FROM events GROUP BY task_id ORDER BY task_id"):
        res = replay_task(conn, r["task_id"])
        print(f"  {r['task_id']:<12} {r['n']:>4} events   {'chain intact' if res.ok else 'DIVERGED'}")
    conn.close()
    return 0


def cmd_health_checks(args) -> int:
    """The instruments that watch the instruments. Spec §5, §11."""
    from evals.health import run_all
    print("== harness health ==")
    warned = 0
    for c in run_all(args.db):
        print(f"  {c}")
        warned += not c.ok
    print(f"\n{warned} warning(s). Gauges, not gates -- nothing here halts a run.")
    return 0


def cmd_dashboard(args) -> int:
    from evals.dashboard import OUT, build
    OUT.write_text(build(args.db))
    print(f"wrote {OUT}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="chingis", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(ROOT / "chingis.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("health", cmd_health), ("submit", cmd_submit),
                     ("status", cmd_status), ("dashboard", cmd_dashboard),
                     ("checks", cmd_health_checks)):
        s = sub.add_parser(name)
        s.set_defaults(func=fn)
        if name in ("health", "submit"):
            s.add_argument("--transport", default="zai", choices=["zai", "codex"])
            s.add_argument("--model", default=None)
        if name == "submit":
            s.add_argument("objective")
            s.add_argument("--repo", default=None)
            s.add_argument("--task-id", default=None)
            s.add_argument("--task-type", default="coding_routine")
            s.add_argument("--usd", type=float, default=0.50)
            s.add_argument("--quota", type=float, default=10.0)
            s.add_argument("--max-events", type=int, default=40)
            s.add_argument("--dry-run", action="store_true",
                           help="scripted executive; spends nothing")

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
