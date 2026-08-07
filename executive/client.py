"""Executive clients. Spec §3.

Prompt layout, with the cache breakpoint where the plan puts it:

    [system.md] + [decision.schema.json inlined]  <- byte-identical across all tasks
    ⟨cache breakpoint⟩
    [ledger] + [event]

Everything before the breakpoint is frozen. A CI test asserts byte-identity across two cold
builds (tests/test_b3_executive.py), because the 1.25x cache-write premium means the schema
changes only at a version bump through the §10 ratchet -- one deliberate re-warm, never
tinkering.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent
SYSTEM_MD = HERE / "prompts" / "system.md"
SCHEMA_JSON = HERE / "schema" / "decision.schema.json"

CONFIDENCE_FLOOR = 0.6
MAX_INVALID_ATTEMPTS = 2


@dataclass
class Decision:
    decision: str
    reason_code: str
    confidence: float
    params: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    forced: str | None = None   # set by the kernel when it overrides the verb

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "decision": self.decision,
                "reason_code": self.reason_code, "confidence": self.confidence,
                "params": self.params}


class InvalidDecision(ValueError):
    pass


def frozen_prefix() -> str:
    """Everything before the cache breakpoint. Byte-identical across all tasks, forever,
    until a deliberate schema_version bump."""
    return SYSTEM_MD.read_text() + "\n\n# Decision schema\n\n```json\n" + \
        SCHEMA_JSON.read_text().rstrip() + "\n```\n"


def validate(raw: dict) -> Decision:
    """Kernel-side re-validation. Defense in depth, not trust.

    Server-enforced structured outputs already constrain the model; this runs anyway,
    because the kernel never assumes the adaptive plane behaved.
    """
    schema = json.loads(SCHEMA_JSON.read_text())
    props = schema["properties"]

    for key in schema["required"]:
        if key not in raw:
            raise InvalidDecision(f"missing required field {key!r}")
    if raw["decision"] not in props["decision"]["enum"]:
        raise InvalidDecision(f"unknown verb {raw['decision']!r}")
    if raw["reason_code"] not in props["reason_code"]["enum"]:
        raise InvalidDecision(f"unknown reason_code {raw['reason_code']!r}")
    conf = raw["confidence"]
    if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        raise InvalidDecision(f"confidence {conf!r} outside [0,1]")
    if not isinstance(raw.get("params"), dict):
        raise InvalidDecision("params must be an object")
    if raw.get("schema_version", 1) != 1:
        raise InvalidDecision(f"schema_version {raw.get('schema_version')} != 1")

    return Decision(decision=raw["decision"], reason_code=raw["reason_code"],
                    confidence=float(conf), params=raw["params"])


def apply_confidence_gate(d: Decision) -> Decision:
    """Confidence < 0.6 => kernel forces escalate regardless of verb. Not advisory."""
    if d.confidence < CONFIDENCE_FLOOR and d.decision != "escalate":
        return Decision("escalate", "low_confidence_escalation", d.confidence,
                        {**d.params, "original_decision": d.decision},
                        forced=f"confidence {d.confidence} < {CONFIDENCE_FLOOR}")
    return d


class Executive(Protocol):
    name: str

    def decide(self, ledger_md: str, event: dict) -> Decision: ...


# --------------------------------------------------------------------- scripted --
class ScriptedExecutive:
    """Deterministic executive for the B3 scenario suite.

    The 10 scenarios test the KERNEL's handling of decisions -- failure, refusal, budget
    squeeze, pivot, outage -- not model quality. A scripted executive is what makes those
    scenarios assertions rather than vibes.
    """

    name = "scripted"

    def __init__(self, script: list[dict] | None = None, default: dict | None = None) -> None:
        self.script = list(script or [])
        self.default = default or {"schema_version": 1, "decision": "halt",
                                   "reason_code": "terminal_success", "confidence": 0.9,
                                   "params": {"status": "success"}}
        self.seen: list[dict] = []

    def decide(self, ledger_md: str, event: dict) -> Decision:
        self.seen.append(event)
        raw = self.script.pop(0) if self.script else dict(self.default)
        return validate(raw)


# ------------------------------------------------------------------------ Luna --
class LunaClient:
    """Phase 1-2 executive: GPT-5.6 Luna, strict structured outputs, cached prefix.

    Reaches Luna through whichever transport is configured:
      "codex" -- ChatGPT-plan OAuth against the Codex coding endpoint (default)
      "zai"   -- the Z.ai coding endpoint, for when the Codex quota is spent
    """

    name = "luna"

    def __init__(self, transport: str = "codex", model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None) -> None:
        self.transport = transport
        self.model = model or ("gpt-5.6-luna" if transport == "codex" else "glm-4.6")
        self.api_key = api_key or os.environ.get("ZAI_API_KEY") or os.environ.get("ZAI_KEY", "")
        self.base_url = base_url or os.environ.get("ZAI_CODING_ENDPOINT") or \
            "https://api.z.ai/api/coding/paas/v4"
        self.invalid_streak = 0
        self.calls = 0

    def healthcheck(self) -> tuple[bool, str]:
        if self.transport == "codex":
            from adapters.codex_oauth import CodexQuotaExhausted, list_models
            try:
                return True, f"codex oauth: {list_models()}"
            except CodexQuotaExhausted as e:
                return False, f"codex quota exhausted (plan={e.plan_type}, {e.resets_in_days:.1f}d)"
            except Exception as e:  # noqa: BLE001
                return False, f"{type(e).__name__}: {e}"
        return (bool(self.api_key), self.base_url if self.api_key else "no ZAI_API_KEY")

    def decide(self, ledger_md: str, event: dict) -> Decision:
        """One wake, one decision. Two invalid attempts force an escalation."""
        prefix = frozen_prefix()
        suffix = (f"# Ledger\n\n{ledger_md}\n\n# Event\n\n```json\n"
                  f"{json.dumps(event, indent=2, sort_keys=True)}\n```\n\n"
                  "Emit one decision object.")

        for attempt in range(1, MAX_INVALID_ATTEMPTS + 1):
            try:
                raw = self._call(prefix, suffix)
                d = validate(raw)
                self.invalid_streak = 0
                return apply_confidence_gate(d)
            except (InvalidDecision, json.JSONDecodeError) as e:
                self.invalid_streak += 1
                suffix += f"\n\nYour previous output was rejected: {e}. Emit valid JSON only."
                if attempt >= MAX_INVALID_ATTEMPTS:
                    # decision_invalid => forced escalate. The kernel does not retry forever.
                    return Decision("escalate", "low_confidence_escalation", 0.0,
                                    {"reason": f"decision_invalid x{attempt}: {e}"},
                                    forced="two invalid decisions")
        raise AssertionError("unreachable")

    def _call(self, prefix: str, suffix: str) -> dict:
        from openai import OpenAI

        self.calls += 1
        if self.transport == "codex":
            from adapters.codex_oauth import respond
            text, _ = respond(self.model, prefix, suffix)
            return json.loads(_strip_fence(text))

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        r = client.chat.completions.create(
            model=self.model, temperature=0,
            messages=[
                # The prefix is one message and never varies -- that is the cache unit.
                {"role": "system", "content": prefix},
                {"role": "user", "content": suffix},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(_strip_fence(r.choices[0].message.content or "{}"))


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        t = t.rsplit("```", 1)[0]
    return t.strip()
