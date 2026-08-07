"""routing_policy.yaml executor. Spec §6, B4.

Policy is data the EXECUTIVE writes. The file ships empty of rules; day one, Chingis routes
every contract by live judgment. Facts are supplied as context, never as rules.

Three properties this module enforces, all of them structural rather than advisory:

1. **Provenance.** Every reflex carries author=executive, the decision id that created it,
   a reason_code, and a TTL. `author()` refuses anything else -- there is no code path by
   which a developer-written rule enters the adaptive plane.
2. **Decay.** Reflexes expire. Rigidity exists only where the model currently chooses it,
   and re-affirming costs a fresh decision.
3. **Reflexability.** Some events can never be reflexed away (operator input, hard budget
   stops, invalid decisions, policy exceptions). Checked in Bus/Runtime, listed in events.py.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from executive.client import Decision

from .events import Event

DEFAULT_POLICY = Path(__file__).resolve().parent.parent / "policy" / "routing_policy.yaml"


@dataclass
class Reflex:
    match: dict[str, Any]
    action: dict[str, Any]
    author: str
    reason_code: str
    created_by: str
    ttl_days: int
    created_ts: str

    def to_dict(self) -> dict:
        return {"match": self.match, "action": self.action, "author": self.author,
                "reason_code": self.reason_code, "created_by": self.created_by,
                "ttl_days": self.ttl_days, "created_ts": self.created_ts}

    def expired(self, now: dt.datetime) -> bool:
        try:
            created = dt.datetime.fromisoformat(self.created_ts.replace("Z", "+00:00"))
        except ValueError:
            return True   # unparseable provenance => treat as expired. Fail closed.
        return now >= created + dt.timedelta(days=self.ttl_days)

    def as_decision(self) -> Decision:
        return Decision("dispatch", self.reason_code, 1.0, dict(self.action))


class PolicyError(ValueError):
    pass


class PolicyRuntime:
    def __init__(self, path: Path | None = None, *, now_fn=None) -> None:
        self.path = Path(path or DEFAULT_POLICY)
        self.reflexes: list[Reflex] = []
        self._now = now_fn or (lambda: dt.datetime.now(dt.UTC))
        self.load()

    # ------------------------------------------------------------------- load --
    def load(self) -> None:
        if not self.path.exists():
            self.reflexes = []
            return
        data = yaml.safe_load(self.path.read_text()) or {}
        self.reflexes = []
        for raw in data.get("reflexes") or []:
            self.reflexes.append(self._parse(raw))

    def _parse(self, raw: dict) -> Reflex:
        missing = [k for k in ("author", "reason_code", "created_by", "ttl_days")
                   if k not in raw]
        if missing:
            raise PolicyError(f"reflex missing provenance: {missing}")
        if raw["author"] != "executive":
            raise PolicyError(
                f"author={raw['author']!r}: developer-authored routing is a CI failure")
        return Reflex(match=raw.get("match", {}), action=raw.get("action", {}),
                      author=raw["author"], reason_code=raw["reason_code"],
                      created_by=raw["created_by"], ttl_days=int(raw["ttl_days"]),
                      created_ts=raw.get("created_ts", "1970-01-01T00:00:00Z"))

    # ------------------------------------------------------------------ author --
    def author(self, patch: dict, *, decision_id: str, reason_code: str) -> Reflex:
        """The ONLY way a reflex is created. Provenance is stamped here, not supplied."""
        ttl = int(patch.get("ttl_days", 30))
        if ttl <= 0:
            raise PolicyError("ttl_days must be positive; an immortal reflex is rigidity")
        if not patch.get("action"):
            raise PolicyError("reflex needs an action")
        reflex = Reflex(
            match=patch.get("match", {}), action=patch["action"],
            author="executive",                       # never taken from the patch
            reason_code=reason_code, created_by=decision_id, ttl_days=ttl,
            created_ts=self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self.reflexes.append(reflex)
        return reflex

    def save(self) -> None:
        self.path.write_text(yaml.safe_dump(
            {"schema_version": 1, "reflexes": [r.to_dict() for r in self.reflexes]},
            sort_keys=False))

    # ------------------------------------------------------------------- match --
    def purge_expired(self) -> int:
        """TTL expiry purges any standing rule the executive stopped re-affirming."""
        now = self._now()
        before = len(self.reflexes)
        self.reflexes = [r for r in self.reflexes if not r.expired(now)]
        return before - len(self.reflexes)

    def match(self, event: Event, ledger, *, now_iso: str | None = None) -> Reflex | None:
        """First live, matching reflex -- or None, which means the executive wakes."""
        if not event.reflexable:
            return None
        self.purge_expired()
        for r in self.reflexes:
            if self._matches(r, event, ledger, now_iso):
                return r
        return None

    def _matches(self, r: Reflex, event: Event, ledger, now_iso: str | None) -> bool:
        m = r.match
        if (want := m.get("event_type")) and str(event.type) != want:
            return False
        if (want := m.get("task_type")) and ledger.state.get("task_type") != want:
            return False
        if window := m.get("window"):
            if not _in_window(window, now_iso or event.ts):
                return False
        return True


def _in_window(window: str, ts_iso: str) -> bool:
    """`"02:00-06:00 America/New_York"` -- the quota-arbitrage shape from spec §6.

    Timezone is honoured when the zone is available; a bad zone fails the match rather than
    silently comparing UTC to local, which would route work into a peak window by accident.
    """
    try:
        spec, _, zone = window.partition(" ")
        start_s, _, end_s = spec.partition("-")
        moment = dt.datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if zone:
            from zoneinfo import ZoneInfo
            moment = moment.astimezone(ZoneInfo(zone.strip()))
        start = dt.time.fromisoformat(start_s)
        end = dt.time.fromisoformat(end_s)
    except Exception:  # noqa: BLE001
        return False
    now_t = moment.time()
    return start <= now_t < end if start <= end else (now_t >= start or now_t < end)
