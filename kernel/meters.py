"""Budget and quota meters. Spec §2, §9.

Checked BEFORE dispatch, not after. Hard stops beat apologetic overruns.

Thresholds fire at 60 / 85 / 100 percent. 60 and 85 wake the executive so it can descope or
checkpoint; 100 is a hard halt the executive does not get a vote on.

Quota is metered exactly like tokens, because on both packaged fleets quota -- not dollars
-- is the scarce resource. `quota_threshold` additionally fires on burn-rate anomaly, not
just totals: a fleet that will be dead in an hour is a problem before it is at 85%.
"""

from __future__ import annotations

from dataclasses import dataclass, field

THRESHOLDS: tuple[int, ...] = (60, 85, 100)


class BudgetExceeded(Exception):
    """Hard stop at 100%. Not catchable into a retry -- that is the whole point."""


@dataclass
class Meter:
    name: str
    limit: float
    spent: float = 0.0
    _fired: set[int] = field(default_factory=set)

    @property
    def pct(self) -> float:
        return 100.0 * self.spent / self.limit if self.limit > 0 else 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)

    def would_exceed(self, amount: float) -> bool:
        return self.spent + amount > self.limit

    def crossings(self, amount: float) -> list[int]:
        """Thresholds this spend would newly cross. Each fires at most once."""
        after = 100.0 * (self.spent + amount) / self.limit if self.limit > 0 else 0.0
        return [t for t in THRESHOLDS if t not in self._fired and after >= t]

    def charge(self, amount: float) -> list[int]:
        crossed = self.crossings(amount)
        self.spent += amount
        self._fired.update(crossed)
        return crossed


class Meters:
    """The set of meters guarding one task."""

    def __init__(self, *, usd: float, quota_units: float, wall_s: float = 0.0) -> None:
        self.usd = Meter("tokens_usd", usd)
        self.quota = Meter("quota_units", quota_units)
        self.wall = Meter("wall_s", wall_s) if wall_s > 0 else None
        self._burn: list[tuple[float, float]] = []  # (ts_seconds, cumulative quota)

    def _all(self) -> list[Meter]:
        return [m for m in (self.usd, self.quota, self.wall) if m is not None]

    def preflight(self, *, usd: float = 0.0, quota: float = 0.0) -> None:
        """Refuse a dispatch that would breach a hard limit. Called BEFORE the adapter runs."""
        if self.usd.would_exceed(usd):
            raise BudgetExceeded(
                f"dispatch would exceed tokens_usd: {self.usd.spent:.4f} + {usd:.4f} "
                f"> {self.usd.limit:.4f}"
            )
        if self.quota.would_exceed(quota):
            raise BudgetExceeded(
                f"dispatch would exceed quota_units: {self.quota.spent:.2f} + {quota:.2f} "
                f"> {self.quota.limit:.2f}"
            )

    def charge(self, *, usd: float = 0.0, quota: float = 0.0, wall_s: float = 0.0) -> list[dict]:
        """Charge actuals. Returns threshold events to emit."""
        out: list[dict] = []
        for meter, amount in ((self.usd, usd), (self.quota, quota)):
            for t in meter.charge(amount):
                out.append({"meter": meter.name, "threshold": t,
                            "pct": round(meter.pct, 2), "spent": round(meter.spent, 6),
                            "limit": meter.limit, "hard_halt": t >= 100})
        if self.wall is not None and wall_s:
            for t in self.wall.charge(wall_s):
                out.append({"meter": "wall_s", "threshold": t, "pct": round(self.wall.pct, 2),
                            "spent": self.wall.spent, "limit": self.wall.limit,
                            "hard_halt": t >= 100})
        return out

    def snapshot(self) -> dict:
        return {
            m.name: {"spent": round(m.spent, 6), "limit": m.limit, "pct": round(m.pct, 1)}
            for m in self._all()
        }


class QuotaLedger:
    """Per-fleet quota accounting with peak multipliers and burn-rate anomaly detection.

    Units per contract are guessed until B4 replaces them with a week of observation.
    `source` records which, so a number nobody measured can never masquerade as one
    somebody did.
    """

    def __init__(self) -> None:
        self._fleets: dict[str, list[tuple[float, float]]] = {}
        self.multipliers: dict[str, float] = {}

    def record(self, fleet: str, units: float, at_s: float) -> None:
        self._fleets.setdefault(fleet, []).append((at_s, units))

    def burn_rate(self, fleet: str, window_s: float, now_s: float) -> float:
        """Units per hour over the trailing window."""
        pts = [(t, u) for t, u in self._fleets.get(fleet, []) if now_s - t <= window_s]
        if not pts:
            return 0.0
        return sum(u for _, u in pts) / (window_s / 3600.0)

    def calibrate(self, fleet: str, *, min_samples: int = 5) -> dict:
        """Replace a guessed units-per-contract with an observed one. Plan §6, B4.

            "Estimate units/contract in week one by observation, store per-fleet
             multipliers ... and let quota_threshold fire on burn-rate anomaly."

        Refuses below `min_samples`. A multiplier derived from two contracts is a guess
        wearing an observation's clothes, and `source` exists precisely so a number nobody
        measured can never masquerade as one somebody did.
        """
        samples = [u for _, u in self._fleets.get(fleet, [])]
        if len(samples) < min_samples:
            return {"fleet": fleet, "source": "estimated", "samples": len(samples),
                    "reason": f"need >= {min_samples} observations, have {len(samples)}"}
        observed = sum(samples) / len(samples)
        self.multipliers[fleet] = observed
        ordered = sorted(samples)
        return {"fleet": fleet, "source": "observed", "samples": len(samples),
                "units_per_contract": round(observed, 3),
                "median": ordered[len(ordered) // 2],
                "min": min(samples), "max": max(samples)}

    def units_for(self, fleet: str, default: float = 1.0) -> tuple[float, str]:
        """Best available estimate, and honestly labelled. Callers that need to know how
        much to trust the number can ask, instead of having to already know."""
        if fleet in self.multipliers:
            return self.multipliers[fleet], "observed"
        return default, "estimated"

    def anomalous(
        self, fleet: str, now_s: float, *, short_s: float = 900, long_s: float = 21600,
        factor: float = 3.0,
    ) -> bool:
        """True when short-window burn far exceeds the long-window baseline.

        Totals tell you the fleet is dying; rate tells you it is about to.
        """
        base = self.burn_rate(fleet, long_s, now_s)
        recent = self.burn_rate(fleet, short_s, now_s)
        return base > 0 and recent > factor * base
