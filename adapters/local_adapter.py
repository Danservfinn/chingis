"""W0 — local fleet. MLX/LM Studio on the M2 Max.

Duties: cheap summarization, log triage, injection pre-screening, and shadow-executive
scoring. GPT-OSS 20B (~14GB) co-resident with dev work; Qwen3.6 35B-A3B (~20GB) headless.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .base import Result, Status

#: Candidates tried in order when W0_URL/MLX_SERVER_URL is unset. MLX's 8080 was the only
#: default, so W0 read as DOWN on a machine with a perfectly good ollama on 11434 -- and a
#: lane that is down for a reachable reason is one nobody investigates. Explicit config
#: still wins; this only decides where to look when nothing was said.
DEFAULT_URLS = ("http://127.0.0.1:11434/v1", "http://127.0.0.1:8080/v1")
DEFAULT_URL = DEFAULT_URLS[0]


class LocalAdapter:
    lane = "W0"

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("W0_URL")
                         or os.environ.get("MLX_SERVER_URL") or self._discover())
        if not self.base_url.rstrip("/").endswith("/v1"):
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.model = model or os.environ.get("W0_MODEL", "qwen3:0.6b")

    def _client(self):
        from openai import OpenAI
        return OpenAI(api_key="local", base_url=self.base_url)

    @staticmethod
    def _discover() -> str:
        """First candidate that answers. Falls back to the first so the error names a URL."""
        import urllib.request
        for url in DEFAULT_URLS:
            try:
                urllib.request.urlopen(url.rstrip("/v1") + "/v1/models", timeout=2)
                return url
            except Exception:                                # noqa: BLE001
                continue
        return DEFAULT_URLS[0]

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
