"""Injected sources of nondeterminism.

Replay is byte-deterministic only if every non-reproducible value the kernel consumes --
wall-clock time, identifiers -- comes from here rather than from the ambient environment.
In live mode these read the world. In replay mode they read the audit log.

This is the whole trick behind B1's acceptance criterion. Anything that calls
`time.time()` or `uuid4()` directly inside the kernel breaks it, so nothing does.
"""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator, Protocol


class Clock(Protocol):
    def now_iso(self) -> str: ...


class IdGen(Protocol):
    def next_id(self, prefix: str) -> str: ...


class SystemClock:
    """Wall clock, UTC, second-resolution ISO-8601."""

    def now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ScriptedClock:
    """Replays timestamps in the order they were originally recorded.

    Exhausting the script is a hard error, not a fallback to the wall clock: a replay that
    silently starts inventing timestamps is no longer a replay.
    """

    def __init__(self, stamps: list[str]) -> None:
        self._it: Iterator[str] = iter(stamps)
        self._last: str | None = None

    def now_iso(self) -> str:
        try:
            self._last = next(self._it)
        except StopIteration:
            raise RuntimeError(
                "ScriptedClock exhausted: replay requested more timestamps than the audit "
                "log contains. The replayed run diverged from the recorded one."
            ) from None
        return self._last


class UuidIdGen:
    def next_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CounterIdGen:
    """Deterministic ids for replay and tests. Per-prefix monotonic counters."""

    def __init__(self, start: int = 1) -> None:
        self._counters: dict[str, Iterator[int]] = {}
        self._start = start

    def next_id(self, prefix: str) -> str:
        if prefix not in self._counters:
            self._counters[prefix] = itertools.count(self._start)
        return f"{prefix}_{next(self._counters[prefix]):05d}"


@dataclass
class Deps:
    """Everything the kernel needs from outside itself."""

    clock: Clock = field(default_factory=SystemClock)
    ids: IdGen = field(default_factory=UuidIdGen)

    @classmethod
    def deterministic(cls, stamps: list[str] | None = None) -> "Deps":
        return cls(
            clock=ScriptedClock(stamps) if stamps is not None else ScriptedClock([]),
            ids=CounterIdGen(),
        )
