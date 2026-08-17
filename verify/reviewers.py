"""Reviewer transports shared by V3 and B0.2. One copy of the contamination guard.

Two consumers need "give this prompt to a specific model and hand back the text":
`v3_spotcheck` and `benchmarks/b02/preflight_parse`. They had begun to grow their own
copies, which is how a guard drifts — and the guard here is the one that decides whether
an experiment measured what it says it measured.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

ZAI_ANTHROPIC = os.environ.get("ZAI_ANTHROPIC_ENDPOINT") or "https://api.z.ai/api/anthropic"

#: Anything that could silently redirect "Anthropic" elsewhere. `ANTHROPIC_API_KEY` also
#: takes precedence over the claude.ai OAuth login, so it goes too — and on this machine a
#: stray one could easily hold a z.ai value.
REDIRECT_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


class FleetContamination(RuntimeError):
    """A reviewer is not reaching the lab it claims to."""


def native_env() -> dict:
    """Environment with every redirect stripped, so OAuth reaches Anthropic itself.

    Without this a leaked `ANTHROPIC_BASE_URL` points the "Anthropic" reviewer at the
    z.ai endpoint, and the run measures GLM reviewing GLM while reporting a cross-lab
    contrast. Nothing fails; the number is simply wrong, which is the worst failure mode
    a verifier has.
    """
    for var in REDIRECT_VARS:
        if "z.ai" in (os.environ.get(var) or "").lower():
            raise FleetContamination(
                f"{var} points at z.ai; refusing to call that lab 'anthropic'")
    return {k: v for k, v in os.environ.items() if k not in REDIRECT_VARS}


def anthropic_oauth(prompt: str, *, model: str | None = None, timeout: int = 300) -> str:
    """Anthropic through the Claude Code subscription. Flat-rate, and pinned by name.

    Pinned because reviewer identity is the variable under test wherever this is used: a
    vendor default that shifts underneath a run would change what was measured without
    anything failing.
    """
    env = native_env()
    env["ANTHROPIC_MODEL"] = model or os.environ.get("V3_ANTHROPIC_MODEL", "claude-sonnet-4-5")
    p = subprocess.run([os.environ.get("CLAUDE_BIN", "claude"), "-p",
                        "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, timeout=timeout, env=env)
    try:
        d = json.loads(p.stdout)
    except (json.JSONDecodeError, AttributeError):
        return p.stdout or ""
    served = (list(d.get("modelUsage") or {}) or [""])[0]
    if served and "glm" in served.lower():
        raise FleetContamination(f"Anthropic reviewer was served {served!r}")
    return d.get("result", "")


def zai_anthropic_endpoint(prompt: str, *, model: str, timeout: int = 300) -> str:
    """A z.ai model by NAME, with the served model asserted.

    Asking for `claude-sonnet-4-5` here and accepting whatever z.ai maps it to is how W2
    stayed labelled `glm-5.2` for months while serving `glm-4.7`.
    """
    key = os.environ.get("ZAI_KEY") or os.environ["ZAI_API_KEY"]
    req = urllib.request.Request(
        ZAI_ANTHROPIC + "/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 4000,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    served = r.get("model") or ""
    if served and served != model:
        raise FleetContamination(
            f"asked z.ai for {model!r} and was served {served!r}; the identity under test "
            "is not the one configured")
    return "".join(b.get("text", "") for b in r.get("content", []))
