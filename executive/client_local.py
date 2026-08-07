"""Phase 3 executive: the local model. Spec §3.

GPT-OSS 20B (~14GB) co-resident with dev work, or Qwen3.6 35B-A3B (~20GB) headless.
MoE ~3B active -> est. 40-70 tok/s, so sub-2s structured decisions -- faster than a cloud
round trip, at a marginal orchestration cost of $0.

Grammar-constrained decoding replaces the server-enforced structured outputs of Phase 1.
The kernel re-validates either way: constrained decoding narrows what the model *can* emit,
it does not change what the kernel is willing to accept.

Not promotable until the §12 Phase-2 bar is met: >=95% agreement on routine verbs over
>=300 events with ZERO capability-gate divergences (executive/shadow.py).
"""

from __future__ import annotations

import json
import os

from .client import (
    Decision,
    InvalidDecision,
    MAX_INVALID_ATTEMPTS,
    apply_confidence_gate,
    frozen_prefix,
    validate,
)


def decision_grammar() -> dict:
    """JSON-schema grammar for constrained decoding (Outlines / llama.cpp / MLX)."""
    from .client import SCHEMA_JSON
    return json.loads(SCHEMA_JSON.read_text())


class LocalExecutive:
    name = "local"

    def __init__(self, base_url: str | None = None, model: str = "local",
                 constrained: bool = True) -> None:
        self.base_url = base_url or os.environ.get("MLX_SERVER_URL", "http://127.0.0.1:8080")
        if not self.base_url.rstrip("/").endswith("/v1"):
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.model = model
        self.constrained = constrained
        self.calls = 0

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key="local", base_url=self.base_url)

    def healthcheck(self) -> tuple[bool, str]:
        try:
            models = [m.id for m in self._client().models.list().data]
            return True, f"ok ({self.base_url}) models={models[:3]}"
        except Exception as e:  # noqa: BLE001
            return False, f"no MLX server at {self.base_url}: {type(e).__name__}"

    def decide(self, ledger_md: str, event: dict) -> Decision:
        prefix = frozen_prefix()
        suffix = (f"# Ledger\n\n{ledger_md}\n\n# Event\n\n```json\n"
                  f"{json.dumps(event, indent=2, sort_keys=True)}\n```\n\n"
                  "Emit one decision object.")

        for attempt in range(1, MAX_INVALID_ATTEMPTS + 1):
            try:
                self.calls += 1
                kwargs: dict = {"model": self.model, "temperature": 0,
                                "messages": [{"role": "system", "content": prefix},
                                             {"role": "user", "content": suffix}]}
                if self.constrained:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": "decision", "strict": True,
                                        "schema": decision_grammar()},
                    }
                r = self._client().chat.completions.create(**kwargs)
                return apply_confidence_gate(validate(json.loads(r.choices[0].message.content or "{}")))
            except (InvalidDecision, json.JSONDecodeError) as e:
                suffix += f"\n\nRejected: {e}. Emit valid JSON only."
                if attempt >= MAX_INVALID_ATTEMPTS:
                    return Decision("escalate", "low_confidence_escalation", 0.0,
                                    {"reason": f"decision_invalid x{attempt}: {e}"},
                                    forced="two invalid decisions")
        raise AssertionError("unreachable")
