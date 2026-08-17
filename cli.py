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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adapters.claude_adapter import ClaudeAdapter            # noqa: E402
from adapters.dsh_adapter import DshAdapter                  # noqa: E402
from adapters.glm_cc_adapter import GlmCodeAdapter          # noqa: E402
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


def build_adapters(registry: Registry) -> dict:
    # W1 and W2 run the SAME packaged tool against DIFFERENT labs, so the cross-fleet
    # contrast varies only the lab. See adapters/claude_adapter.py for why that is a
    # better pairing than the Codex-vs-GLM one the plan specified.
    #
    # W3 is a THIRD packaged shell (dsh) whose route is a string: one adapter reaches
    # z.ai, Anthropic, xAI, and DeepSeek. After B0's FAIL verdict it is justified on
    # routing grounds only -- metered billing beside the flat-rate fleets, and a
    # reroute target for refusals that needs no new subscription. It is NOT a
    # verification lane. See adapters/dsh_adapter.py.
    return {"WR": RawAdapter(registry), "W1": ClaudeAdapter(),
            "W2": GlmCodeAdapter(), "W3": DshAdapter(), "W0": LocalAdapter()}


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
        policy=PolicyRuntime(), verifier=V1Runner(), screener=Screener(),
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
