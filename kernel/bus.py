"""Event bus: the loop, the queue, seq numbering. Spec §1, §2.

The kernel is deliberately dumb. It validates, meters, executes policy, and emits events.
It holds no opinions about the task.

Ordering contract, which replay depends on:
  1. An event is assigned its seq and appended to the audit log BEFORE its effects apply.
  2. Handlers are invoked in (priority, seq) order -- deterministic given the same inputs.
  3. Anything a handler emits is queued, never executed inline. No reentrancy, no surprises.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .audit import AuditLog
from .deps import Deps
from .events import Event, EventType

Handler = Callable[[Event, "Bus"], Awaitable[None] | None]


@dataclass(order=True)
class _Queued:
    priority: int
    tiebreak: int
    event: Event = field(compare=False)


class Bus:
    """Single-task event loop.

    One Bus per task_id keeps seq numbering trivially correct and the hash chain per-task,
    as the audit design requires.
    """

    def __init__(
        self,
        task_id: str,
        audit: AuditLog,
        deps: Deps | None = None,
        *,
        replay: bool = False,
    ) -> None:
        self.task_id = task_id
        self.audit = audit
        self.deps = deps or Deps()
        self.replay = replay
        self._queue: list[_Queued] = []
        self._counter = itertools.count()
        self._handlers: dict[EventType, list[Handler]] = {}
        self._any: list[Handler] = []
        self.processed: list[Event] = []
        self.halted = False
        self.halt_reason: str | None = None
        last_seq, _ = audit.head(task_id)
        self._next_seq = last_seq + 1

    # ------------------------------------------------------------ registration --
    def on(self, event_type: EventType, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def on_any(self, handler: Handler) -> None:
        """Wakes for every event. This is the executive's subscription.

        The kernel forwards, it does not filter. Only an executive-authored reflex may
        short-circuit a wake, and reflexes are applied in policy_runtime, not here.
        """
        self._any.append(handler)

    # ----------------------------------------------------------------- emitting --
    def emit(self, type: EventType, payload: dict[str, Any] | None = None) -> Event:
        """Append to the audit log and queue for handling. Append happens first."""
        event = Event(
            task_id=self.task_id,
            seq=self._next_seq,
            ts=self.deps.clock.now_iso(),
            type=type,
            payload=payload or {},
        )
        self._next_seq += 1
        self.audit.append(event)
        heapq.heappush(self._queue, _Queued(event.priority, next(self._counter), event))
        return event

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    # -------------------------------------------------------------------- loop --
    async def run(self, max_events: int = 10_000) -> list[Event]:
        """Drain the queue. Returns events in the order they were processed."""
        n = 0
        while self._queue and not self.halted:
            if n >= max_events:
                raise RuntimeError(
                    f"bus exceeded {max_events} events on {self.task_id} -- "
                    "a handler is almost certainly emitting in a cycle"
                )
            n += 1
            event = heapq.heappop(self._queue).event
            self.processed.append(event)
            for handler in (*self._handlers.get(event.type, ()), *self._any):
                result = handler(event, self)
                if hasattr(result, "__await__"):
                    await result
                if self.halted:
                    break
        return self.processed
