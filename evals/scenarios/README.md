# scenarios/ — the B3 gate

Ten scripted orchestration scenarios. **All ten green is the B3 acceptance criterion**, and
B3 green is where the stack freeze lifts (target Sep 13).

1. clean dispatch
2. fail → retry ok
3. fail ×2 → reroute
4. refusal → reroute
5. refusal ×2 → request_human
6. budget 85% → checkpoint / descope
7. verify_failed → revised retry
8. peak-window route override
9. outage → drain
10. ambiguous → request_human

Scripted, deterministic, replayable. These test the executive's judgment against known
event sequences — not model quality.
