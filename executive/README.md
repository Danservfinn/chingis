# executive/ — ADAPTIVE PLANE

**Empty by design.** Built at **B3** (Aug 31 – Sep 13). The stack freeze lifts on B3 green.

| File | Holds |
|---|---|
| `prompts/system.md` | frozen prefix v1 — byte-identity test in CI |
| `schema/decision.schema.json` | **written** — the action space, frozen within a version |
| `ledger.py` | sections, 3k-token cap, compaction |
| `client_luna.py` | cache breakpoint after prefix; strict structured outputs |
| `client_local.py` | Phase 2+; Outlines-constrained decoding |

The executive is a reducer: `(ledger, event) -> decision`. It never sees raw transcripts.
One structured decision per wake.

## Why `prompts/system.md` is not written yet

Writing the frozen prefix before B0 reports would be premature, and not only on ground-rule
grounds. The prefix inlines the action space, and the action space contains `verify` with
V2 semantics. **If B0 fails, V2 is dropped from the design** — the action space changes,
and a prefix frozen today would have to be re-warmed on day one, paying the 1.25× write
premium the cache discipline exists to avoid.

Prompt layout, for when it is written:

```
[system.md — role, invariants, action semantics]
[decision.schema.json inlined]
⟨cache breakpoint⟩          <- everything above is byte-identical across all tasks
[ledger]
[event]
```

**B3 acceptance — all 10 scenarios green:** clean dispatch · fail→retry ok · fail×2→reroute ·
refusal→reroute · refusal×2→request_human · budget 85%→checkpoint/descope ·
verify_failed→revised retry · peak-window route override · outage→drain · ambiguous→request_human.
