"""WR — the raw lane. Spec §4, plan §6.

    messages -> model (tool defs: bash, read_file, apply_patch)
             -> kernel-gated execution in the worktree
             -> append result -> repeat
    until the model emits done or exhausts tool_calls / tokens_usd.

No inner harness: context assembly, tool sequencing, and in-lane retry limits are specified
by the executive in the contract. Metered per token, and every step is transparent to the
audit log -- raw-lane trajectories are first-class flywheel data.

Provider: the Z.ai coding endpoint (operator direction, 2026-08-06). OpenRouter is
deliberately not used.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from kernel.capabilities import Registry, parse_contract_capabilities

from .base import Result, Status, detect_refusal
from .tools import TOOL_SCHEMAS, ToolBox, ToolCall

DEFAULT_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
DEFAULT_MODEL = "glm-4.6"


class RawAdapter:
    """The faithful path. The dumb shell does not exist here at any layer."""

    lane = "WR"

    def __init__(
        self,
        registry: Registry,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        trace: list[dict] | None = None,
    ) -> None:
        self.registry = registry
        # ZAI_API_KEY against the coding endpoint. OPENAI_BASE_URL is deliberately NOT
        # consulted: on this machine it points at OpenRouter, which we do not use.
        self.api_key = api_key or os.environ.get("ZAI_API_KEY") or os.environ.get("ZAI_KEY", "")
        self.base_url = base_url or os.environ.get("ZAI_CODING_ENDPOINT") or DEFAULT_BASE_URL
        self.trace = trace if trace is not None else []

    # ------------------------------------------------------------- healthcheck --
    def healthcheck(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "no API key (ZAI_API_KEY / ZAI_KEY)"
        try:
            client = self._client()
            client.models.list()
            return True, f"ok ({self.base_url})"
        except Exception as e:  # noqa: BLE001 - healthcheck reports, never raises
            return False, f"{type(e).__name__}: {e}"

    def _client(self):
        from openai import OpenAI

        return OpenAI(api_key=self.api_key, base_url=self.base_url)

    # ---------------------------------------------------------------- the loop --
    def run(self, contract: dict, worktree: Path) -> Result:
        started = time.time()
        budget = contract["budget"]
        model = contract.get("model") or DEFAULT_MODEL
        cap = parse_contract_capabilities(
            self.registry, worker="WR", caps=contract["capabilities"], worktree=worktree
        )
        box = ToolBox(self.registry, cap.token, worktree)

        system = (
            "You are a worker executing one contract inside a git worktree.\n"
            "Work only inside the worktree. Use the tools; do not describe changes you "
            "have not made. When the objective is satisfied, reply with a short summary "
            "and stop calling tools.\n\n"
            f"Objective:\n{contract['objective']}\n\n"
            f"Relevant paths: {', '.join(contract.get('context_refs', []))}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Begin. Inspect what you need, then make the change."},
        ]

        client = self._client()
        usd = 0.0
        tokens_in = tokens_out = 0
        calls_made = 0
        max_calls = int(budget.get("tool_calls", 40))
        max_usd = float(budget.get("tokens_usd", 1.0))
        final_text = ""
        status = Status.DONE

        while True:
            if calls_made >= max_calls:
                status = Status.FAILED
                final_text += f"\n[kernel] tool_calls budget exhausted ({max_calls})"
                break
            if usd >= max_usd:
                status = Status.FAILED
                final_text += f"\n[kernel] tokens_usd budget exhausted (${max_usd})"
                break

            try:
                resp = client.chat.completions.create(
                    model=model, messages=messages, tools=TOOL_SCHEMAS,
                    tool_choice="auto", temperature=0,
                )
            except Exception as e:  # noqa: BLE001
                return Result(Status.FAILED, {"error": f"{type(e).__name__}: {e}"},
                              {"wall_s": time.time() - started, "usd": usd},
                              lane=self.lane, model=model)

            # `usage.cost` is an OpenRouter field. The z.ai coding endpoint does not
            # return it, so this read produced 0.0 on EVERY call and `usd` was always
            # exactly zero -- which silently made success-per-dollar, M0's headline
            # metric and the spec's stated basis for the founding claim, uncomputable.
            # Tokens are what the endpoint actually reports, so tokens are what we count.
            # Dollars are derived only where a price is configured; an unpriced model
            # yields None, never 0.0, because "free" and "unknown" are different claims.
            u = getattr(resp, "usage", None)
            if u is not None:
                tokens_in += int(getattr(u, "prompt_tokens", 0) or 0)
                tokens_out += int(getattr(u, "completion_tokens", 0) or 0)
                usd += float(getattr(u, "cost", 0.0) or 0.0)
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            self.trace.append({"step": calls_made, "tokens_in": tokens_in,
                               "tokens_out": tokens_out,
                               "finish_reason": resp.choices[0].finish_reason})

            if not getattr(msg, "tool_calls", None):
                final_text = msg.content or ""
                break

            for tc in msg.tool_calls:
                calls_made += 1
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = box.dispatch(ToolCall(tc.function.name, args))
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": out.output[:8000]})

        from .base import Worktree  # local import keeps base import-light

        diff = _git_diff(worktree)
        refusal = detect_refusal(final_text, diff_empty=not diff.strip())
        if refusal:
            status = Status.REFUSED

        return Result(
            status=status,
            artifacts={"diff": diff, "summary": final_text[:4000],
                       "tool_calls": box.calls},
            # The `usd` key is OMITTED when the endpoint priced nothing, rather than set
            # to None: the kernel meters with `cost.get("usd", 0.0)`, and a present-but-None
            # value defeats that default. Absent means unknown to every reader -- the meter
            # charges zero, and the outcome recorder writes NULL rather than 0.0.
            raw_cost={**({"usd": round(usd, 6)} if usd else {}),
                      "tokens_in": tokens_in, "tokens_out": tokens_out,
                      "wall_s": round(time.time() - started, 2),
                      "quota_units_est": 0.0, "tool_calls": calls_made},
            refusal_signal=refusal,
            lane=self.lane,
            model=model,
        )


def _git_diff(worktree: Path) -> str:
    import subprocess

    subprocess.run(["git", "-C", str(worktree), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(worktree), "diff", "--cached"],
                          capture_output=True, text=True).stdout
