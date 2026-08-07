# evals/

Protected path — the eval suite sits outside the loop's write set. The instruments are not
allowed to learn.

- `scenarios/` — the 10 scripted orchestration scenarios that gate B3.
- `heldout/` — **write-protected, operator-rotated monthly.** Each month the operator moves
  ~20% of benchmark contracts here and logs the manifest hash. The loop never trains on
  anything whose hash appears here.
- `dashboard.py` — sqlite → static HTML agreement/eval board (B6). Static HTML. No web UI.

Held-out rotation is the defense against memorization, which is what M4 tests: gains that
fail to transfer mean the loop memorized and the interesting claim is dead.
