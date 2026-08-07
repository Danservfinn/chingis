"""W0 — local fleet. MLX/LM Studio on the M2 Max.

Duties: cheap summarization, log triage, injection pre-screening, and shadow-executive
scoring. GPT-OSS 20B (~14GB) co-resident with dev work; Qwen3.6 35B-A3B (~20GB) headless.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .base import Result, Status

DEFAULT_URL = "http://127.0.0.1:8080/v1"


class LocalAdapter:
    lane = "W0"

    def __init__(self, base_url: str | None = None, model: str = "local") -> None:
        self.base_url = base_url or os.environ.get("MLX_SERVER_URL", DEFAULT_URL)
        if not self.base_url.rstrip("/").endswith("/v1"):
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.model = model

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key="local", base_url=self.base_url)

    def healthcheck(self) -> tuple[bool, str]:
        try:
            models = [m.id for m in self._client().models.list().data]
            return True, f"ok ({self.base_url}) models={models[:3]}"
        except Exception as e:  # noqa: BLE001
            return False, f"no MLX server at {self.base_url}: {type(e).__name__}"

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        r = self._client().chat.completions.create(
            model=self.model, temperature=0, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return r.choices[0].message.content or ""

    def run(self, contract: dict, worktree: Path) -> Result:
        started = time.time()
        try:
            text = self.complete("You are a cheap local worker.", contract["objective"])
        except Exception as e:  # noqa: BLE001
            return Result(Status.FAILED, {"error": str(e)},
                          {"wall_s": time.time() - started}, lane=self.lane)
        return Result(Status.DONE, {"summary": text},
                      {"wall_s": round(time.time() - started, 2), "usd": 0.0},
                      lane=self.lane, model=self.model)
