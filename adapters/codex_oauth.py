"""ChatGPT-plan OAuth against the Codex coding endpoint.

Operator direction (2026-08-06) overrides the spec §4 default of driving `codex exec`:
W1 may be consumed via the plan's own OAuth credentials against its own coding endpoint.
This is still plan-native auth talking to a plan-native endpoint -- it is not a raw API key
pointed at a subscription -- but it is a step away from spec §0's "subscriptions are agents,
not endpoints", and it is recorded here so the deviation is visible rather than implicit.

Credentials come from ~/.codex/auth.json, written by `codex login`. This module never
writes credentials and never logs a token.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AUTH_PATH = Path.home() / ".codex" / "auth.json"
CACHE_PATH = Path.home() / ".codex" / "models_cache.json"
BASE = "https://chatgpt.com/backend-api/codex"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # public Codex CLI client id


class CodexAuthError(RuntimeError):
    pass


class CodexQuotaExhausted(RuntimeError):
    """HTTP 429 usage_limit_reached. Carries the reset time so the executive can route
    around the fleet rather than retry into a wall."""

    def __init__(self, message: str, resets_at: int | None, plan_type: str | None) -> None:
        super().__init__(message)
        self.resets_at = resets_at
        self.plan_type = plan_type

    @property
    def resets_in_days(self) -> float | None:
        return (self.resets_at - time.time()) / 86400 if self.resets_at else None


@dataclass
class CodexAuth:
    access_token: str
    account_id: str
    client_version: str

    @classmethod
    def load(cls, path: Path = AUTH_PATH) -> "CodexAuth":
        if not path.exists():
            raise CodexAuthError(f"{path} not found -- run `codex login` first")
        data = json.loads(path.read_text())
        tokens = data.get("tokens") or {}
        if not tokens.get("access_token"):
            raise CodexAuthError(f"{path} has no access_token (auth_mode={data.get('auth_mode')!r})")
        cv = "0.146.0"
        if CACHE_PATH.exists():
            cv = json.loads(CACHE_PATH.read_text()).get("client_version", cv)
        return cls(tokens["access_token"], tokens.get("account_id", ""), cv)

    def headers(self, *, stream: bool = False) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.access_token}",
            "chatgpt-account-id": self.account_id,
            "originator": "codex_cli_rs",
            "User-Agent": f"codex_cli_rs/{self.client_version}",
            "Content-Type": "application/json",
        }
        if stream:
            h["Accept"] = "text/event-stream"
            h["OpenAI-Beta"] = "responses=experimental"
        return h


def refresh(path: Path = AUTH_PATH) -> CodexAuth:
    """Exchange the refresh token for a new access token and persist it.

    Called on 401. A 429 is NOT an auth problem and must not trigger a refresh -- burning
    the refresh token against a quota wall would turn a temporary block into a permanent one.
    """
    data = json.loads(path.read_text())
    rt = (data.get("tokens") or {}).get("refresh_token")
    if not rt:
        raise CodexAuthError("no refresh_token; run `codex login`")

    body = json.dumps({"client_id": CLIENT_ID, "grant_type": "refresh_token",
                       "refresh_token": rt, "scope": "openid profile email"}).encode()
    req = urllib.request.Request(TOKEN_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        tok = json.loads(r.read().decode())

    data["tokens"]["access_token"] = tok["access_token"]
    if "refresh_token" in tok:
        data["tokens"]["refresh_token"] = tok["refresh_token"]
    data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)
    return CodexAuth.load(path)


def _raise_for_quota(payload: str) -> None:
    try:
        err = json.loads(payload).get("error", {})
    except json.JSONDecodeError:
        return
    if err.get("type") == "usage_limit_reached":
        raise CodexQuotaExhausted(err.get("message", "usage limit reached"),
                                  err.get("resets_at"), err.get("plan_type"))


def request(path: str, *, method: str = "GET", body: dict | None = None,
            params: dict | None = None, auth: CodexAuth | None = None,
            timeout: int = 120, stream: bool = False) -> tuple[int, str]:
    """One request against the Codex endpoint, with a single 401 refresh-and-retry."""
    auth = auth or CodexAuth.load()
    params = dict(params or {})
    params.setdefault("client_version", auth.client_version)
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"

    for attempt in (1, 2):
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=auth.headers(stream=stream),
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            payload = e.read().decode()
            if e.code == 429:
                _raise_for_quota(payload)
                return e.code, payload
            if e.code == 401 and attempt == 1:
                auth = refresh()
                continue
            return e.code, payload
    raise CodexAuthError("unreachable")


def list_models(auth: CodexAuth | None = None) -> list[str]:
    status, body = request("/models", auth=auth)
    if status != 200:
        raise CodexAuthError(f"models: HTTP {status} {body[:200]}")
    d = json.loads(body)
    return [m.get("id") or m.get("slug") for m in d.get("models", d if isinstance(d, list) else [])]


def respond(model: str, instructions: str, prompt: str,
            auth: CodexAuth | None = None, timeout: int = 300) -> tuple[str, dict]:
    """Non-streaming-ish call against /responses. Returns (text, usage)."""
    body = {
        "model": model,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "stream": True,
        "store": False,
    }
    status, payload = request("/responses", method="POST", body=body,
                              auth=auth, timeout=timeout, stream=True)
    if status != 200:
        _raise_for_quota(payload)
        raise CodexAuthError(f"responses: HTTP {status} {payload[:300]}")

    text, usage = [], {}
    for line in payload.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "response.output_text.delta":
            text.append(ev.get("delta", ""))
        elif ev.get("type") == "response.completed":
            usage = ev.get("response", {}).get("usage", {}) or {}
    return "".join(text), usage
