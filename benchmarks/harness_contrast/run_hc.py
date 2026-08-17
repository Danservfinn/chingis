#!/usr/bin/env python3
"""B1H — harness contrast. Three harnesses, ONE model, ONE verifier.

    ./benchmarks/harness_contrast/run_hc.py --arms WR,W3,KUBLAI
    ./benchmarks/harness_contrast/run_hc.py --arms WR --limit 1     # cheap smoke

The question is narrow on purpose: **holding the model constant, does harness
architecture change task success?**

    WR      Chingis' own kernel-gated tool loop      glm-5.2 @ z.ai coding endpoint
    W3      dsh, packaged, driven headless           glm-5.2 @ z.ai via pi-ai
    KUBLAI  Nous hermes-agent, `--profile kublai`    glm-5.2, pinned with -m

This is the fix for the confound that sank B0. There, W1 and W2 differed in lab AND the
result could not separate "different lab" from "different reviewer". Here every arm runs
**the same model at the same lab**, so a difference between arms is attributable to the
harness -- the tool loop, the context assembly, the retry behaviour -- and to nothing else.

## One verifier, three harnesses

Each arm produces a diff and nothing else. Every diff is then applied to an IDENTICAL,
freshly built worktree on this machine and judged by Chingis' own `V1Runner`. No arm
verifies itself, no arm's own notion of success is consulted, and Kublai's diff crosses
the network as a patch rather than as a claim. A harness that says "done" and produced
nothing scores exactly as a harness that says nothing.

## Safety around Kublai

Kublai is a live production agent in Telegram-only incident mode. This runner:
  * uses `hermes -z` (one-shot), which starts no gateway and sends no message
  * uses `--worktree`, so Hermes works in its own isolated git worktree
  * never touches BlueBubbles, Signal, Telegram, launchd, provider config, or credentials
  * pins the model with `-m` for the duration of one CLI call, changing nothing on disk

The gateway is checked for health before and after and the run aborts if it is not `ok`.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from adapters.base import Result, Status                      # noqa: E402
from adapters.dsh_adapter import DshAdapter                   # noqa: E402
from adapters.raw_adapter import RawAdapter                   # noqa: E402
from benchmarks.harness_contrast.contracts import (CONTRACTS,  # noqa: E402
                                                   as_contract, fixture_files)
from kernel.capabilities import Registry                      # noqa: E402
from verify.v1_runners import V1Runner                        # noqa: E402

MODEL = "glm-5.2"
SSH_HOST = "kublai-mini"
REMOTE_ROOT = "/tmp/hc_bench"
HERMES_PATH = "export PATH=$PATH:/opt/homebrew/bin:$HOME/.local/bin"
OUT = Path(__file__).resolve().parent / "results"

#: The z.ai coding endpoint throttles under sustained load, and this benchmark is
#: sustained load by construction: three arms x five contracts against one endpoint.
#: Hermes' own overnight loop hit the same wall (HTTP 529 / code-1305, 2026-08-12).
OVERLOAD_MARKERS = ("429", "529", "overloaded", "rate limit", "code-1305",
                    "temporarily overloaded")


def looks_throttled(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in OVERLOAD_MARKERS)


@dataclass
class ArmResult:
    arm: str
    contract_id: str
    status: str
    v1_pass: bool | None
    pytest_pass: bool | None
    diff_scope_pass: bool | None
    test_weakening: bool
    out_of_scope: list = field(default_factory=list)
    diff_bytes: int = 0
    wall_s: float = 0.0
    note: str = ""
    #: Provider refused to serve (429/529/overload). B0's rule: an apparatus failure is
    #: EXCLUDED from the denominator, never counted as a miss. A throttled arm did not
    #: fail the task -- it never got to attempt it, and scoring that as incapability is
    #: how a benchmark invents a result.
    excluded: bool = False


# ------------------------------------------------------------------ fixtures --
def build_fixture(spec: dict, dest: Path) -> Path:
    """Materialize one task as a fresh git repo. Identical bytes for every arm."""
    subprocess.run(["rm", "-rf", str(dest)], check=True)
    for rel, content in fixture_files(spec).items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)
    subprocess.run(["git", "-c", "user.email=hc@bench", "-c", "user.name=hc",
                    "commit", "-qm", "fixture"], cwd=dest, check=True)
    return dest


def fixture_is_failing(wt: Path) -> bool:
    """Sanity: the task must actually be broken before an arm touches it.

    A fixture whose tests already pass would hand every arm a free win and the whole
    contrast would read as a tie.
    """
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=wt,
                       capture_output=True, text=True, timeout=120)
    return p.returncode != 0


# ---------------------------------------------------------------- local arms --
def run_local(arm: str, contract: dict, wt: Path) -> Result:
    if arm == "WR":
        adapter = RawAdapter(Registry(), model=MODEL) if _raw_takes_model() \
            else RawAdapter(Registry())
        c = {**contract, "lane": "WR", "model": MODEL}
    elif arm == "W3":
        adapter = DshAdapter(provider="zai", model=MODEL)
        c = {**contract, "lane": "W3", "model": f"zai/{MODEL}"}
    else:
        raise ValueError(arm)
    return adapter.run(c, wt)


def _raw_takes_model() -> bool:
    import inspect
    return "model" in inspect.signature(RawAdapter.__init__).parameters


# --------------------------------------------------------------- kublai arm --
def _ssh(cmd: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "ConnectTimeout=10", SSH_HOST, cmd],
                          capture_output=True, text=True, timeout=timeout)


def kublai_health() -> str:
    p = _ssh("curl -s --max-time 5 http://127.0.0.1:8644/health", timeout=40)
    return (p.stdout or "").strip()


def run_kublai(spec: dict, contract: dict) -> tuple[Result, str]:
    """Drive Hermes one-shot on the Mini and bring home a patch, not a claim."""
    remote = f"{REMOTE_ROOT}/{spec['id']}"
    started = time.time()

    # Build the identical fixture remotely, from the same source of truth.
    setup = [f"rm -rf {remote}", f"mkdir -p {remote}"]
    for rel, content in fixture_files(spec).items():
        setup.append(f"mkdir -p {remote}/{Path(rel).parent}")
        setup.append(f"cat > {remote}/{rel} <<'HC_EOF'\n{content}HC_EOF")
    setup += [f"cd {remote} && git init -q && git add -A && "
              f"git -c user.email=hc@bench -c user.name=hc commit -qm fixture"]
    p = _ssh(" && ".join(setup) if False else "\n".join(setup), timeout=120)
    if p.returncode != 0:
        return Result(Status.FAILED, {"error": f"remote fixture failed: {p.stderr[:300]}"},
                      {}, lane="KUBLAI", model=MODEL), ""

    prompt = (f"{contract['objective']}\n\n"
              f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
              "Work only inside this checkout. Make the change and stop.")
    wall = int(float(contract["budget"]["wall_min"]) * 60)
    cmd = (f"{HERMES_PATH}; cd {remote} && "
           f"timeout {wall} hermes --profile kublai -m {MODEL} --yolo "
           f"-z {shlex.quote(prompt)} 2>&1 | tail -40")
    p = _ssh(cmd, timeout=wall + 120)
    summary = (p.stdout or "")[-4000:]

    # The artifact is the patch. Hermes may work in its own --worktree; we asked it to
    # stay in the checkout, so the diff is taken there.
    d = _ssh(f"cd {remote} && git add -A && git diff --cached", timeout=120)
    diff = d.stdout or ""
    throttled = not diff.strip() and looks_throttled(summary)
    status = Status.DONE if diff.strip() else Status.FAILED
    if throttled:
        summary = "[THROTTLED — excluded from denominator] " + summary
    return (Result(status, {"diff": diff, "summary": summary},
                   {"wall_s": round(time.time() - started, 2)},
                   lane="KUBLAI", model=MODEL), diff)


# -------------------------------------------------------------- verification --
def verify(contract: dict, result: Result, spec: dict, workdir: Path) -> ArmResult:
    """Apply the arm's diff to a pristine worktree and judge it with Chingis' own V1.

    Rebuilding the fixture rather than reusing the arm's own directory is the point:
    it means an arm cannot pass by leaving helpful untracked state behind, and Kublai
    -- whose work happened on another machine -- is judged by exactly the same code
    path as the local arms.
    """
    wt = build_fixture(spec, workdir)
    diff = (result.artifacts or {}).get("diff", "") or ""
    note = ""
    if diff.strip():
        p = subprocess.run(["git", "apply", "--whitespace=nowarn", "-"], cwd=wt,
                           input=diff, capture_output=True, text=True)
        if p.returncode != 0:
            note = f"patch did not apply: {p.stderr.strip()[:160]}"

    summary = (result.artifacts or {}).get("summary", "") or ""
    excluded = "[THROTTLED" in summary or (
        not diff.strip() and looks_throttled(summary + " " + str((result.artifacts or {}).get("error", ""))))
    v1 = V1Runner().run_v1(contract, result, worktree=wt)
    checks = {c["check"]: c for c in v1.get("checks", [])}
    scope = checks.get("diff_scope", {})
    pytest_c = checks.get("pytest", {})
    return ArmResult(
        arm=result.lane, contract_id=contract["contract_id"], status=str(result.status),
        v1_pass=bool(v1.get("pass")), pytest_pass=pytest_c.get("pass"),
        diff_scope_pass=scope.get("pass"), test_weakening=bool(scope.get("test_weakening")),
        out_of_scope=scope.get("out_of_scope", []) or [],
        diff_bytes=len(diff), wall_s=(result.raw_cost or {}).get("wall_s", 0.0), note=note,
        excluded=excluded,
    )


# --------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", default="WR,W3,KUBLAI")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0,
                    help="skip the first N contracts, so a long run can be batched")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="seconds to sleep between runs; the shared endpoint throttles "
                         "under the sustained load this benchmark is by construction")
    ap.add_argument("--tag", default="rows",
                    help="results/<tag>.json, so batches do not overwrite each other")
    args = ap.parse_args()

    arms = [a.strip().upper() for a in args.arms.split(",") if a.strip()]
    specs = CONTRACTS[args.start:]
    if args.limit:
        specs = specs[:args.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    work = ROOT / ".worktrees" / "hc"
    work.mkdir(parents=True, exist_ok=True)

    if "KUBLAI" in arms:
        h = kublai_health()
        print(f"[hc] kublai gateway pre-check: {h or '(no response)'}")
        if '"status": "ok"' not in h:
            print("[hc] ABORT: Kublai gateway is not healthy. Not running against a "
                  "production agent in an unknown state.")
            return 1

    rows: list[ArmResult] = []
    for spec in specs:
        contract = as_contract(spec)
        probe = build_fixture(spec, work / f"probe_{spec['id']}")
        if not fixture_is_failing(probe):
            print(f"[hc] SKIP {spec['id']}: fixture already passes; it would be a free win")
            continue
        for arm in arms:
            if args.pace and rows:
                time.sleep(args.pace)
            print(f"[hc] {spec['id']} -> {arm} ...", flush=True)
            try:
                if arm == "KUBLAI":
                    res, _ = run_kublai(spec, contract)
                else:
                    wt = build_fixture(spec, work / f"{arm}_{spec['id']}")
                    res = run_local(arm, contract, wt)
            except Exception as e:                       # noqa: BLE001
                res = Result(Status.FAILED, {"error": str(e)[:300]}, {}, lane=arm, model=MODEL)
            row = verify(contract, res, spec, work / f"verify_{arm}_{spec['id']}")
            row.arm = arm
            rows.append(row)
            print(f"      status={row.status} pytest={row.pytest_pass} "
                  f"scope={row.diff_scope_pass} weakened={row.test_weakening} "
                  f"{row.wall_s}s {row.note}")

    (OUT / f"{args.tag}.json").write_text(json.dumps([asdict(r) for r in rows], indent=2))
    report(rows, arms)
    if "KUBLAI" in arms:
        print(f"\n[hc] kublai gateway post-check: {kublai_health() or '(no response)'}")
    return 0


def report(rows: list[ArmResult], arms: list[str]) -> None:
    print("\n=== B1H harness contrast — one model (glm-5.2), one verifier ===\n")
    print(f"{'arm':<8} {'solved':>7} {'n':>3} {'weakened':>9} {'median s':>9}")
    print("-" * 42)
    lines = []
    for arm in arms:
        allr = [x for x in rows if x.arm == arm]
        r = [x for x in allr if not x.excluded]
        if not allr:
            continue
        dropped = len(allr) - len(r)
        if dropped:
            print(f"{'':<8} ({dropped} excluded: provider throttled, never attempted)")
        if not r:
            continue
        solved = sum(1 for x in r if x.pytest_pass and x.diff_scope_pass)
        weak = sum(1 for x in r if x.test_weakening)
        times = sorted(x.wall_s for x in r)
        med = times[len(times) // 2] if times else 0.0
        print(f"{arm:<8} {solved:>7} {len(r):>3} {weak:>9} {med:>9.1f}")
        lines.append((arm, solved, len(r), weak, med))
    print("\nper contract:")
    for cid in sorted({x.contract_id for x in rows}):
        cells = []
        for arm in arms:
            m = [x for x in rows if x.arm == arm and x.contract_id == cid]
            cells.append(f"{arm}={'PASS' if m and m[0].pytest_pass and m[0].diff_scope_pass else 'fail'}")
        print(f"  {cid:<22} " + "  ".join(cells))
    n = len({x.contract_id for x in rows})
    print(f"\nn={n} contracts per arm. This is a SMOKE-SCALE CONTRAST, not a powered")
    print("experiment: at this n a one-task difference is noise, and no threshold was")
    print("pre-registered because none could be met. Read it as a shakeout of the")
    print("apparatus and a direction to look, never as a ranking.")


if __name__ == "__main__":
    raise SystemExit(main())
