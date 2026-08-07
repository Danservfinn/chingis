# kernel/ — FIXED PLANE

**Empty by design. Ground rule 1: B0 before any harness code.**

Built at **B1** (due Sun Aug 16, ~14h). Operator-writable only — a protected path.

| File | Holds |
|---|---|
| `bus.py` | event loop, queue, seq numbering |
| `events.py` | the event types in spec §2 |
| `capabilities.py` | token registry, scope checks |
| `meters.py` | $ budget, quota units, thresholds 60/85/100 |
| `policy_runtime.py` | `routing_policy.yaml` executor + reflex fast-paths |
| `audit.py` | append-only writer; hash chain per task |
| `replay.py` | deterministic re-run from the audit log |

**B1 acceptance:** pytest replays a 50-event scripted session byte-deterministically from
the audit log; capability denial and the 60/85/100 budget thresholds fire correctly.
No adapters, no executive — scripted fake events only.

The kernel is deliberately dumb: it validates, meters, executes policy, and emits events.
It holds no opinions about the task. ~1–2k lines, immutable at runtime. If a future
feature requires softening a capability check, a budget stop, or an audit write, the
feature is wrong.
