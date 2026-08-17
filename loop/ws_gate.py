"""The WS gate. Spec §10, and the integration plan's Phase 4.

WS is the sovereign lane: dsh composed with the self-referential `tool-cordis` toolset,
where the executive defines, runs, and disposes its own tools as dynamic plugins inside
its own session. It is the Rev H thesis made executable — a harness that is authored by
the model rather than around it.

It is not wired, and this module is why.

    The plan opens WS on "the B0 verdict plus one clean V2/V2.5 cycle." B0 returned
    FAIL on 2026-08-17 and V2 was dropped from the design. The gate was therefore
    conditioned on a verification stack that no longer exists, and it cannot open on
    its original terms.

That is a real finding and not a formality to route around. The premise of bounded
self-improvement is that the BOUND is a verifier the improving thing does not control.
Chingis' surviving stack is V1 (mechanical: does it build, do the tests pass, did the
diff stay in scope) and V3 (spot-check sampling). Neither reads a *design*. A tool the
executive wrote to make its own work easier is exactly the artifact V1 cannot judge: it
can pass every test it ships with and still be the wrong tool, and V1 will say PASS.

So WS stays shut until someone decides -- deliberately, on the record, with a threshold
fixed before the data exists -- what a sufficient reviewer of a self-authored tool is.
That decision is the operator's, and it is a *new pre-registration*, not a re-reading of
this file. Restating the gate in easier terms because the hard terms failed is the exact
move `benchmarks/b0/PREREGISTRATION.md` exists to prevent, and B0's own report already
said the words: **a qualified pass is not a pass.**

There is deliberately no environment variable, no config key, and no `force=` argument
here. A gate with an override is a gate that will be overridden at 2am by whoever is
closest to the keyboard, and the one thing this gate guards is the loop's ability to
change itself.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B0_REPORT = ROOT / "benchmarks" / "b0" / "results" / "report.md"
WS_COMPOSITION = ROOT / "fleets" / "dsh" / "ws.cordis.yml"


class WsGateClosed(RuntimeError):
    """Raised on any attempt to run the sovereign lane. Never caught to proceed."""


def _b0_condition() -> dict:
    """B0 must have run AND not failed. Today it ran and failed."""
    if not B0_REPORT.exists():
        return {"name": "B0 has reported", "met": False,
                "why": "no benchmarks/b0/results/report.md; the cross-fleet question is "
                       "unmeasured and nothing may be built on an assumed answer"}
    text = B0_REPORT.read_text()
    failed = bool(re.search(r"FAIL\s*[—-]", text))
    return {"name": "B0 did not FAIL", "met": not failed,
            "why": ("B0 returned FAIL on 2026-08-17: +10.0 pp against a pre-registered "
                    ">=15 pp bar, n=10, McNemar p=1.0000, and the two directions "
                    "disagreed in sign. V2 was dropped as the pre-registered consequence."
                    if failed else "B0's report does not record a FAIL verdict")}


def _reviewer_condition() -> dict:
    """Something must be able to judge a self-authored tool. Nothing currently can."""
    return {
        "name": "a reviewer exists that V1 cannot stand in for",
        "met": False,
        "why": ("verification is V1+V3. V1 is mechanical and a self-authored tool passes "
                "the tests it ships with; V3 samples but does not review design. No "
                "cross-lineage review survives B0, so a tool the executive wrote would be "
                "judged only by the executive's own work product"),
    }


def _operator_condition() -> dict:
    """The successor bar is an operator decision, taken before any WS data exists."""
    return {
        "name": "a successor pre-registration is signed",
        "met": False,
        "why": ("no pre-registration defines what a sufficient reviewer of a self-authored "
                "tool is, or what result would falsify the WS arm. Writing that bar AFTER "
                "seeing WS output is the failure the ratchet exists to prevent"),
    }


def ws_gate_status() -> dict:
    """Report every condition and whether it holds. Used by the operator dashboard and
    by the refusal message, so the two can never disagree."""
    conditions = [_b0_condition(), _reviewer_condition(), _operator_condition()]
    return {
        "open": all(c["met"] for c in conditions),
        "conditions": conditions,
        "composition_present": WS_COMPOSITION.exists(),
    }


def require_ws_gate() -> None:
    """Refuse to run the sovereign lane. Call this before anything WS-shaped.

    Raises:
        WsGateClosed: always, at time of writing, naming every unmet condition.
    """
    status = ws_gate_status()
    if status["open"]:
        return
    unmet = "\n".join(f"  - {c['name']}: {c['why']}"
                      for c in status["conditions"] if not c["met"])
    raise WsGateClosed(
        "The WS (sovereign) lane is closed. Unmet conditions:\n" + unmet +
        "\n\nOpening it means writing a NEW pre-registration with a bar fixed before "
        "the data exists -- not editing this file. See loop/ws_gate.py for why the "
        "original gate cannot open on its own terms."
    )
