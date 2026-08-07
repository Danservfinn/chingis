"""Event types. Spec §2 table.

Every event is a wake. The kernel forwards, it does not filter -- only a reflex the
executive itself authored may short-circuit one, and it expires unless re-affirmed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


def canonical_json(obj: Any) -> str:
    """The one serialization used for hashing. Sorted keys, no incidental whitespace.

    Every hash in the audit chain depends on this being stable, so it is defined once and
    never inlined anywhere else.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class EventType(StrEnum):
    TASK_RECEIVED = "task_received"
    WORKER_DONE = "worker_done"
    WORKER_FAILED = "worker_failed"
    WORKER_REFUSED = "worker_refused"
    VERIFY_FAILED = "verify_failed"
    BUDGET_THRESHOLD = "budget_threshold"
    QUOTA_THRESHOLD = "quota_threshold"
    TIMEOUT = "timeout"
    POLICY_EXCEPTION = "policy_exception"
    HUMAN_INPUT = "human_input"
    DECISION_INVALID = "decision_invalid"


#: Events the executive must always see, even if a reflex claims to handle them.
#: A reflex that could swallow an operator message or a hard budget stop would be a
#: developer-shaped hole in the "every event is a wake" invariant.
NEVER_REFLEXABLE: frozenset[EventType] = frozenset(
    {
        EventType.HUMAN_INPUT,
        EventType.DECISION_INVALID,
        EventType.POLICY_EXCEPTION,
        EventType.BUDGET_THRESHOLD,
    }
)

#: Priority for the bus queue. Lower sorts first. Operator input outranks everything.
PRIORITY: dict[EventType, int] = {
    EventType.HUMAN_INPUT: 0,
    EventType.BUDGET_THRESHOLD: 1,
    EventType.QUOTA_THRESHOLD: 1,
    EventType.DECISION_INVALID: 2,
    EventType.WORKER_REFUSED: 3,
    EventType.WORKER_FAILED: 3,
    EventType.VERIFY_FAILED: 3,
    EventType.TIMEOUT: 3,
    EventType.WORKER_DONE: 4,
    EventType.POLICY_EXCEPTION: 4,
    EventType.TASK_RECEIVED: 5,
}


@dataclass(frozen=True, slots=True)
class Event:
    task_id: str
    seq: int
    ts: str
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)

    def hashable(self) -> str:
        """The bytes that enter the hash chain. Deliberately excludes audit_hash itself."""
        return canonical_json(
            {
                "task_id": self.task_id,
                "seq": self.seq,
                "ts": self.ts,
                "type": str(self.type),
                "payload": self.payload,
            }
        )

    @property
    def priority(self) -> int:
        return PRIORITY.get(self.type, 5)

    @property
    def reflexable(self) -> bool:
        return self.type not in NEVER_REFLEXABLE
