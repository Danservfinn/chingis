"""The orchestration engine: bus + executive + adapters + meters + verification.

Every event wakes the executive. The kernel forwards, it does not filter -- only a reflex
the executive itself authored may short-circuit a wake, and it expires unless re-affirmed.

The kernel executes verbs; it does not second-guess them. What it does enforce, always:
schema validation, the confidence gate, capability scope, budget preflight, and the audit
write that precedes every effect.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from executive.client import Decision, Executive, apply_confidence_gate
from executive.ledger import Ledger

from .audit import AuditLog
from .bus import Bus
from .capabilities import Registry
from .deps import Deps
from .events import Event, EventType
from .meters import BudgetExceeded, Meters, QuotaLedger
from .screen import Screener, screen_v1_report

MAX_RETRIES_PER_CONTRACT = 3
MAX_REFUSALS_BEFORE_HUMAN = 2
#: Consecutive failures on one lane before the kernel drains it. Above the retry
#: ceiling so a normal fail->retry->reroute sequence never trips it.
LANE_FAILURES_BEFORE_DRAIN = 3


@dataclass
class TaskState:
    task_id: str
    objective: str
    contracts: dict[str, dict] = field(default_factory=dict)
    retries: dict[str, int] = field(default_factory=dict)
    refusals: dict[str, int] = field(default_factory=dict)
    drained_lanes: set[str] = field(default_factory=set)
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    awaiting_human: str | None = None
    checkpoints: list[dict] = field(default_factory=list)
    halted_status: str | None = None


class Runtime:
    def __init__(
        self,
        task_id: str,
        executive: Executive,
        adapters: dict[str, Any],
        *,
        audit: AuditLog,
        meters: Meters,
        registry: Registry | None = None,
        deps: Deps | None = None,
        ledger: Ledger | None = None,
        policy: Any = None,
        verifier: Any = None,
        screener: Screener | None = None,
        contract_store: Any = None,
        workdir: Path | None = None,
    ) -> None:
        self.deps = deps or Deps()
        self.bus = Bus(task_id, audit, self.deps)
        self.executive = executive
        self.adapters = adapters
        self.meters = meters
        self.registry = registry or Registry(self.deps)
        self.ledger = ledger or Ledger()
        self.policy = policy
        self.verifier = verifier
        # Spec §7: raw worker text never enters the executive context. Always present --
        # a Runtime without a screener would be one where the guarantee is optional.
        self.screener = screener or Screener()
        # Contracts persist: a task that cannot be resumed has no checkpoint, and
        # outcomes would be labels for questions nobody kept.
        self.contract_store = contract_store
        self.quota = QuotaLedger()
        self.workdir = workdir or Path(".worktrees")
        self.state = TaskState(task_id, "")
        self.reflex_hits: list[dict] = []
        self.decision_latencies: list[int] = []
        self._in_reflex: str | None = None
        self.bus.on_any(self._wake)

    # ------------------------------------------------------------------- entry --
    def submit(self, objective: str, plan: list[str] | None = None) -> None:
        self.state.objective = objective
        self.ledger.goal = objective
        if plan:
            self.ledger.plan = list(plan)
        self.ledger.set_budget(self.meters.snapshot())
        self.bus.emit(EventType.TASK_RECEIVED, {"objective": objective})

    async def run(self, max_events: int = 500) -> TaskState:
        await self.bus.run(max_events=max_events)
        return self.state

    # -------------------------------------------------------------------- wake --
    def _wake(self, event: Event, bus: Bus) -> None:
        # A reflex may short-circuit, but never for events the design forbids reflexing,
        # and never for an event another reflex just caused.
        #
        # That second condition is load-bearing. Spec §6: "The kernel executes live
        # reflexes deterministically BETWEEN wakes." A reflex that fires on an event its
        # own action produced is not a fast-path, it is an unsupervised control loop -- and
        # since reflexes are model-authored, nothing upstream stops the executive writing
        # one by accident. The kernel bounds it structurally rather than trusting the
        # author to notice.
        caused_by_reflex = bool(event.payload.get("_reflex"))
        if self.policy is not None and event.reflexable and not caused_by_reflex:
            reflex = self.policy.match(event, self.ledger, now_iso=event.ts)
            if reflex is not None:
                self.reflex_hits.append({"seq": event.seq, "reflex": reflex.to_dict()})
                self._in_reflex = reflex.created_by
                try:
                    self._execute(reflex.as_decision(), event, bus)
                finally:
                    self._in_reflex = None
                return

        # The ledger cap is enforced here, not requested politely. Crossing it forces
        # compression before any other verb.
        if self.ledger.over_cap:
            note = self.ledger.compress()
            self.ledger.state["_forced_compression"] = note

        raw_event = {"type": str(event.type), "seq": event.seq, "payload": event.payload}
        # §3 targets sub-2s routine decisions and §11 names "latency stacking" as a failure
        # mode; decisions.latency_ms existed as a column with nothing writing to it, so the
        # mode was undetectable by construction.
        t0 = time.perf_counter()
        decision = self.executive.decide(self.ledger.render(), raw_event)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        decision = apply_confidence_gate(decision)
        self.decision_latencies.append(latency_ms)
        self.state.decisions.append(decision)
        self._execute(decision, event, bus)

    # ---------------------------------------------------------------- dispatch --
    def _emit(self, bus: Bus, etype: EventType, payload: dict) -> Event:
        """Emit with reflex provenance attached. The marker is real audit data: it records
        which reflex caused an event, and it is what the cycle guard in _wake reads."""
        if self._in_reflex:
            payload = {**payload, "_reflex": self._in_reflex}
        return bus.emit(etype, payload)

    def _execute(self, d: Decision, event: Event, bus: Bus) -> None:
        handler = getattr(self, f"_verb_{d.decision}", None)
        if handler is None:
            self._emit(bus, EventType.DECISION_INVALID, {"verb": d.decision, "reason": "no handler"})
            return
        handler(d, event, bus)

    # ------------------------------------------------------------------- verbs --
    def _verb_dispatch(self, d: Decision, event: Event, bus: Bus) -> None:
        lane = d.params.get("lane", "WR")
        contract = self._contract_for(d, lane)

        if lane in self.state.drained_lanes:
            self._emit(bus, EventType.POLICY_EXCEPTION,
                     {"reason": f"lane {lane} is drained", "contract_id": contract["contract_id"]})
            return

        adapter = self.adapters.get(lane)
        if adapter is None:
            self._emit(bus, EventType.POLICY_EXCEPTION, {"reason": f"no adapter for lane {lane}"})
            return

        # Budget is checked BEFORE dispatch. A refused preflight must not charge.
        try:
            self.meters.preflight(usd=float(contract["budget"].get("tokens_usd", 0)),
                                  quota=float(contract["budget"].get("quota_units", 0)))
        except BudgetExceeded as e:
            self._emit(bus, EventType.BUDGET_THRESHOLD,
                     {"meter": "preflight", "threshold": 100, "hard_halt": True,
                      "detail": str(e)})
            bus.halt(f"budget: {e}")
            return

        started = time.time()
        worktree = self.workdir / f"{self.state.task_id}_{contract['contract_id']}_{lane}"

        # Each contract executes in a disposable git worktree: the diff is the artifact,
        # the worktree is the blast radius. The RUNTIME owns that lifecycle -- an adapter
        # handed a path that does not exist cannot be contained, and every tool call in it
        # fails on cwd before any capability check gets a say.
        repo = contract.get("repo")
        v1: dict = {}
        try:
            if repo:
                from adapters.base import Worktree

                with Worktree(repo, contract.get("base_ref", "HEAD"), worktree):
                    result = adapter.run(contract, worktree)
                    # V1 runs inside the worktree, before teardown and before any model
                    # sees the artifact.
                    v1 = self._run_v1(contract, result, worktree)
            else:
                worktree.mkdir(parents=True, exist_ok=True)
                result = adapter.run(contract, worktree)
                v1 = self._run_v1(contract, result, worktree)
        except Exception as e:  # noqa: BLE001 -- an adapter crash is a worker_failed, not ours
            self._emit(bus, EventType.WORKER_FAILED,
                     {"contract_id": contract["contract_id"], "lane": lane,
                      "error": f"{type(e).__name__}: {e}"})
            return

        wall = time.time() - started
        cost = getattr(result, "raw_cost", {}) or {}
        for th in self.meters.charge(usd=float(cost.get("usd", 0.0)),
                                     quota=float(cost.get("quota_units_est", 0.0))):
            self._emit(bus, EventType.BUDGET_THRESHOLD if th["meter"] != "quota_units"
                       else EventType.QUOTA_THRESHOLD, th)
            if th.get("hard_halt"):
                bus.halt(f"{th['meter']} reached 100%")
        self.quota.record(lane, float(cost.get("quota_units_est", 0.0)), started)
        self.ledger.set_budget(self.meters.snapshot())

        status = str(getattr(result, "status", "failed"))

        # --- injection containment (spec §7, plan §6) -----------------------------
        # Everything below this line that originated with a worker is screened before it
        # can become an event payload, because event payloads are what the executive
        # reads. refusal_signal, adapter errors, and V1 stdout tails are all
        # worker-influenced text, and all three previously reached the executive raw.
        arts = getattr(result, "artifacts", None) or {}
        screened = self.screener.screen(
            str(arts.get("summary") or arts.get("stdout_tail") or ""),
            source=f"worker:{lane}")
        v1 = screen_v1_report(v1, self.screener)

        payload = {"contract_id": contract["contract_id"], "lane": lane,
                   "wall_s": round(wall, 2), "cost": cost,
                   "worker": screened.to_payload()}

        if status == "done":
            # A success clears the streak: drain is for sustained outage, not for one bad
            # contract that happened to land on this lane.
            self.state.consecutive_failures[lane] = 0
        if status == "refused":
            sig = self.screener.screen(str(result.refusal_signal or ""),
                                       source=f"refusal:{lane}")
            payload["refusal_signal"] = sig.summary if sig.flagged else result.refusal_signal
            self.state.refusals[lane] = self.state.refusals.get(lane, 0) + 1
            self.ledger.record_outcome(f"{contract['contract_id']} refused on {lane}")
            self._emit(bus, EventType.WORKER_REFUSED, payload)
        elif status == "timeout":
            self.ledger.record_outcome(f"{contract['contract_id']} timed out on {lane}")
            self._emit(bus, EventType.TIMEOUT, payload)
        elif status == "failed":
            self._note_lane_failure(lane, bus)
            err = self.screener.screen(str((result.artifacts or {}).get("error", "")),
                                       source=f"error:{lane}")
            payload["error"] = err.summary if err.flagged else \
                str((result.artifacts or {}).get("error", ""))[:2000]
            self.ledger.record_outcome(f"{contract['contract_id']} failed on {lane}")
            self._emit(bus, EventType.WORKER_FAILED, payload)
        else:
            payload["v1"] = v1
            self.ledger.record_outcome(
                f"{contract['contract_id']} done on {lane}, V1={'pass' if v1.get('pass') else 'fail'}")
            if v1 and not v1.get("pass", True):
                self._emit(bus, EventType.VERIFY_FAILED, {**payload, "tier": "V1", "detail": v1})
            else:
                self._emit(bus, EventType.WORKER_DONE, payload)

    def _run_v1(self, contract: dict, result: Any, worktree: Path | None = None) -> dict:
        """V1 is kernel-run, deterministic, free, and always first -- before any model sees
        the artifact."""
        if self.verifier is None:
            return {}
        try:
            return self.verifier.run_v1(contract, result, worktree)
        except TypeError:
            return self.verifier.run_v1(contract, result)

    def _verb_retry(self, d: Decision, event: Event, bus: Bus) -> None:
        cid = d.params.get("contract_id") or event.payload.get("contract_id", "")
        n = self.state.retries.get(cid, 0) + 1
        self.state.retries[cid] = n
        if n > MAX_RETRIES_PER_CONTRACT:
            self._emit(bus, EventType.POLICY_EXCEPTION,
                     {"reason": "retry ceiling reached", "contract_id": cid, "retries": n})
            return
        self._verb_dispatch(d, event, bus)

    def _verb_route(self, d: Decision, event: Event, bus: Bus) -> None:
        self.ledger.state["last_route"] = f"{d.params.get('lane')} ({d.reason_code})"
        self._verb_dispatch(d, event, bus)

    def _verb_race(self, d: Decision, event: Event, bus: Bus) -> None:
        for lane in d.params.get("lanes", ["WR", "W1"]):
            self._verb_dispatch(
                Decision("dispatch", d.reason_code, d.confidence, {**d.params, "lane": lane}),
                event, bus)

    def _verb_verify(self, d: Decision, event: Event, bus: Bus) -> None:
        tier = d.params.get("verifier", "V2")
        if self.verifier is None:
            self._emit(bus, EventType.POLICY_EXCEPTION, {"reason": f"{tier} unavailable"})
            return
        verdict = self.verifier.run(tier, d.params, self.state.contracts)
        self.ledger.record_outcome(f"{tier} verdict: {verdict.get('verdict')}")
        if not verdict.get("pass", True):
            self._emit(bus, EventType.VERIFY_FAILED, {"tier": tier, **verdict})

    def _verb_escalate(self, d: Decision, event: Event, bus: Bus) -> None:
        target = d.params.get("target_tier", "sol")
        self.ledger.state["escalation"] = f"{target}: {d.reason_code}"
        if target == "operator":
            self.state.awaiting_human = d.params.get("question", d.reason_code)
            bus.halt(f"escalated to operator: {self.state.awaiting_human}")

    def _verb_compress_ledger(self, d: Decision, event: Event, bus: Bus) -> None:
        self.ledger.record_outcome(self.ledger.compress())

    def _verb_revise_plan(self, d: Decision, event: Event, bus: Bus) -> None:
        if plan := d.params.get("plan"):
            self.ledger.plan = list(plan)
        self.ledger.state["plan_revised"] = d.reason_code

    def _verb_edit_policy(self, d: Decision, event: Event, bus: Bus) -> None:
        if self.policy is None:
            self._emit(bus, EventType.POLICY_EXCEPTION, {"reason": "no policy runtime"})
            return
        patch = d.params.get("policy_patch") or {}
        try:
            # event.ts, not the wall clock: reflex authoring stays inside the same
            # determinism guarantee as everything else the kernel writes.
            entry = self.policy.author(patch, decision_id=f"d_{len(self.state.decisions):05d}",
                                       reason_code=d.reason_code, now_iso=event.ts)
            # Persist, or the reflex exists only for this process and B4's "observed in
            # audit with provenance + TTL" is unverifiable after the run ends.
            if getattr(self.policy, "path", None) is not None:
                self.policy.save()
            self.ledger.record_outcome(
                f"authored reflex {entry.reason_code} ttl={entry.ttl_days}d "
                f"by {entry.created_by}")
        except ValueError as e:
            self._emit(bus, EventType.DECISION_INVALID, {"verb": "edit_policy", "reason": str(e)})

    def _verb_request_human(self, d: Decision, event: Event, bus: Bus) -> None:
        q = d.params.get("question", d.reason_code)
        self.state.awaiting_human = q
        self.ledger.open_questions.append(q)
        bus.halt(f"request_human: {q}")

    def _verb_checkpoint(self, d: Decision, event: Event, bus: Bus) -> None:
        self.state.checkpoints.append({"seq": event.seq, "ledger_hash": self.ledger.hash(),
                                       "budget": self.meters.snapshot()})

    def _verb_halt(self, d: Decision, event: Event, bus: Bus) -> None:
        self.state.halted_status = d.params.get("status", "success")
        bus.halt(f"halt: {self.state.halted_status}")

    # ----------------------------------------------------------------- helpers --
    def _contract_for(self, d: Decision, lane: str) -> dict:
        cid = d.params.get("contract_id") or f"c_{len(self.state.contracts):04d}"
        if cid in self.state.contracts:
            # A retry is a REVISED retry: whatever the executive restated in params
            # replaces the prior contract's field. Without this, "verify_failed -> revised
            # retry" silently re-runs the identical contract and fails identically.
            revisable = ("objective", "context_refs", "capabilities", "budget",
                         "verification", "output_schema", "repo", "base_ref")
            contract = {**self.state.contracts[cid], "lane": lane,
                        **{k: d.params[k] for k in revisable if k in d.params}}
        else:
            contract = {
                "contract_id": cid, "lane": lane,
                "objective": d.params.get("objective", self.state.objective),
                "context_refs": d.params.get("context_refs", ["**"]),
                "capabilities": d.params.get("capabilities", ["fs:worktree", "net:none"]),
                "output_schema": "diff+summary",
                # The repo/ref the worktree is cut from. Without these the runtime cannot
                # create a worktree and the lane runs against a bare directory.
                # The operator names the repo (ledger state); the executive names the
                # objective and the lane. A model that could choose its own blast radius
                # would be choosing its own containment.
                "repo": d.params.get("repo") or self.ledger.state.get("repo"),
                "base_ref": d.params.get("base_ref", self.ledger.state.get("base_ref", "HEAD")),
                "budget": d.params.get("budget", {"wall_min": 10, "quota_units": 1,
                                                  "tokens_usd": 0.10, "tool_calls": 20,
                                                  "inlane_retries": 0}),
                "verification": d.params.get("verification", ["V1:pytest"]),
            }
        if model := d.params.get("model"):
            contract["model"] = model
        self.state.contracts[cid] = contract
        if self.contract_store is not None:
            try:
                self.contract_store.put(contract, self.state.task_id,
                                        self.deps.clock.now_iso(), status="pending")
            except Exception as e:  # noqa: BLE001
                # An invalid contract is the executive's error, not a kernel crash.
                self.ledger.state["_contract_rejected"] = f"{cid}: {e}"
        return contract

    def _note_lane_failure(self, lane: str, bus: Bus) -> None:
        """Sustained failure on one lane is an outage, and §11's answer to an outage is to
        drain it. Manual drain alone means a dead provider keeps receiving dispatches until
        an operator notices -- exactly the stall the event model exists to prevent.

        The threshold is consecutive failures, not total: a lane that fails once and then
        succeeds is healthy, and draining it would cost the executive an option for no
        reason.
        """
        n = self.state.consecutive_failures.get(lane, 0) + 1
        self.state.consecutive_failures[lane] = n
        if n >= LANE_FAILURES_BEFORE_DRAIN and lane not in self.state.drained_lanes:
            self.drain(lane)
            self._emit(bus, EventType.POLICY_EXCEPTION,
                       {"reason": f"lane {lane} drained after {n} consecutive failures",
                        "lane": lane, "auto_drained": True})

    def drain(self, lane: str) -> None:
        """Provider outage: take a lane out of rotation. Healthcheck-driven at B6."""
        self.state.drained_lanes.add(lane)
        self.ledger.state[f"lane_{lane}"] = "drained"
