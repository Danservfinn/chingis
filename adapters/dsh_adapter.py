"""W3 — the dsh fleet. Spec §4.

    contract -> prompt -> `dsh --profile headless --patch w3.cordis.yml`
             -> the harness's own tool loop, rooted at the worktree
             -> final answer on stdout + the diff -> Result

A PACKAGED lane, like W1 and W2: Chingis does not drive the inner tool loop and cannot
see into it mid-run. What makes it worth having is not transparency (see the honesty
note below) but ROUTING -- which, after B0's FAIL verdict, is the only ground the
multi-fleet topology still stands on:

  * **Billing arbitrage.** W2 spends flat-rate GLM coding-plan quota. W3 reaches the
    same lab, the same endpoint, metered per token. When quota is the scarce resource
    and dollars are not, that is a different lane even at identical model weights.
  * **Reroute target.** One adapter reaches four labs (z.ai, Anthropic, xAI, DeepSeek)
    by changing one string, so a refusal on any fleet has somewhere to go that does
    not need a new adapter, a new CLI, or a new subscription.
  * **No new subscription.** W1's Codex allowance is exhausted until ~2026-09-01. W3
    needs an API key, not a plan.

Honesty about the inner shell: dsh persists sessions as zstd-compressed JSONL under
its own home, and in the stock headless composition that file carries the session
HEADER only -- no per-step tool calls. So this lane is *auditable in principle* and
*not step-transparent in practice today*. It is treated here exactly as W1/W2 are:
opaque, judged by its diff. Do not let the fact that the harness is open source get
recorded as evidence that this run was observable. See fleets/dsh/README.md.

Transport: the CLI, as a subprocess, like every other packaged lane. Deliberately NOT
the Python SDK -- `deepseek-harness` on PyPI is an unrelated third party's package,
and the real one (`deepseek-harness-sdk`) is a preview that would add a dependency to
buy nothing this lane needs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .base import (Result, Status, detect_refusal, strip_tooling_artifacts,
                   worktree_diff)

ROOT = Path(__file__).resolve().parent.parent
CORDIS_PATCH = ROOT / "fleets" / "dsh" / "w3.cordis.yml"
DSH_BIN = ROOT / "fleets" / "dsh" / "node_modules" / ".bin" / "dsh"
# dsh needs a Node with zstd (>= 23.8); the machine's brew node is 23.7, so the lane
# vendors its own rather than mutating the operator's toolchain. See fleets/dsh/README.md.
NODE_BIN_DIR = ROOT / "fleets" / "dsh" / ".runtime" / "node-v24.19.0-darwin-arm64" / "bin"

DEFAULT_PROVIDER = "zai"
DEFAULT_MODEL = "glm-5.3"

# Must stay in lockstep with the routes declared in w3.cordis.yml; a drift means this
# adapter clears a route dsh then refuses, or vice versa. A test asserts the equality.
PROVIDER_KEY_ENV: dict[str, str] = {
    "zai": "ZAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class DshAdapter:
    """The glass-box-in-principle packaged lane. One adapter, four labs."""

    lane = "W3"

    def __init__(
        self,
        provider: str = DEFAULT_PROVIDER,
        model: str = DEFAULT_MODEL,
        *,
        dsh_bin: str | None = None,
        dsh_home: Path | None = None,
        node_bin_dir: Path | None = None,
        key_env_override: dict[str, str] | None = None,
    ) -> None:
        self.provider, self.model = provider, model
        self.dsh_bin = dsh_bin or os.environ.get("DSH_BIN") or str(DSH_BIN)
        # Session state lives here, NEVER in the worktree: it is flywheel data, and a
        # session file inside the blast radius would ride into the diff and be
        # reviewed as part of the worker's change.
        self.dsh_home = Path(dsh_home or os.environ.get("DSH_HOME")
                             or ROOT / "runs" / "w3-home").resolve()
        self.node_bin_dir = Path(node_bin_dir or NODE_BIN_DIR)
        self.key_env = {**PROVIDER_KEY_ENV, **(key_env_override or {})}

    # ------------------------------------------------------------- health --
    def healthcheck(self) -> tuple[bool, str]:
        if not (Path(self.dsh_bin).exists() or shutil.which(self.dsh_bin)):
            return False, f"dsh not found at {self.dsh_bin} (run: npm install in fleets/dsh)"
        if not CORDIS_PATCH.exists():
            return False, f"W3 composition missing: {CORDIS_PATCH}"
        ok, why = self._credential(self.provider)
        return (True, f"ok ({self.provider}/{self.model})") if ok else (False, why)

    def _credential(self, provider: str) -> tuple[bool, str]:
        ref = self.key_env.get(provider)
        if ref is None:
            return False, (f"W3: provider route {provider!r} is not declared in "
                           f"{CORDIS_PATCH.name}; declare it there and in PROVIDER_KEY_ENV")
        if not os.environ.get(ref, "").strip():
            return False, (f"W3: no credential for provider route {provider!r}; "
                           f"its profile resolves {ref}, which is not set")
        return True, ref

    # ---------------------------------------------------------------- run --
    def run(self, contract: dict, worktree: Path) -> Result:
        started = time.time()
        provider, model = self._route(contract)
        stamp = f"{provider}/{model}"

        # Fail fast, Chingis-side. Letting dsh discover this produces a MISSING_CREDENTIAL
        # on stdout that the refusal heuristic would then read as worker prose -- a
        # configuration error wearing a worker's clothes.
        ok, why = self._credential(provider)
        if not ok:
            return Result(Status.FAILED, {"error": why}, {"wall_s": 0.0},
                          lane=self.lane, model=stamp)

        wall = max(1, int(float(contract["budget"].get("wall_min", 25)) * 60))
        cmd = [self.dsh_bin, "--profile", "headless",
               "--patch", str(CORDIS_PATCH), self._prompt(contract)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=wall,
                               cwd=str(worktree), env=self._env(provider, model))
        except subprocess.TimeoutExpired:
            return Result(Status.TIMEOUT, {"diff": worktree_diff(worktree)},
                          {"wall_s": round(time.time() - started, 2)},
                          lane=self.lane, model=stamp)
        except OSError as e:
            # A missing or non-executable binary is a lane outage, not a traceback.
            return Result(Status.FAILED, {"error": f"W3: cannot exec {self.dsh_bin}: {e}"},
                          {"wall_s": round(time.time() - started, 2)},
                          lane=self.lane, model=stamp)

        text = (p.stdout or "").strip()
        stripped = strip_tooling_artifacts(worktree)
        diff = worktree_diff(worktree)
        refusal = detect_refusal(text, diff_empty=not diff.strip())
        status = (Status.REFUSED if refusal else
                  Status.DONE if p.returncode == 0 else Status.FAILED)
        return Result(
            status,
            {"diff": diff, "summary": text[:8000],
             "stderr_tail": (p.stderr or "")[-2000:],
             "stripped_tooling_artifacts": stripped},
            # No quota_units_est: this lane does not consume a plan allowance. That
            # absence is the whole economic point of it, so it is not faked with a 1.0.
            {"wall_s": round(time.time() - started, 2)},
            refusal_signal=refusal, lane=self.lane, model=stamp,
        )

    # ------------------------------------------------------------ helpers --
    def _route(self, contract: dict) -> tuple[str, str]:
        """`model` may be "provider/model" to move a contract between labs without
        touching the lane, which is what makes W3 a reroute target."""
        raw = contract.get("model")
        if not raw:
            return self.provider, self.model
        return tuple(raw.split("/", 1)) if "/" in raw else (self.provider, raw)

    def _prompt(self, contract: dict) -> str:
        return (f"{contract['objective']}\n\n"
                f"Relevant paths: {', '.join(contract.get('context_refs', []))}\n"
                "Work only inside this checkout. Make the change and stop.")

    def _env(self, provider: str, model: str) -> dict[str, str]:
        env = {**os.environ,
               "DSH_HOME": str(self.dsh_home),
               "DSH_PROVIDER": provider,
               "DSH_MODEL": model,
               # Stated, not inherited: worker artifacts do not phone home.
               "DSH_TELEMETRY_MODE": "DISABLED"}
        if self.node_bin_dir.is_dir():
            env["PATH"] = f"{self.node_bin_dir}{os.pathsep}{env.get('PATH', '')}"
        self.dsh_home.mkdir(parents=True, exist_ok=True)
        return env
