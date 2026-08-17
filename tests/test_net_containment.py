"""Network containment for the WR lane. Spec §4, and a gap found 2026-08-17.

`net:none` appears on every Chingis contract and was **decorative**: `Registry.check_net`
existed but was never called from anywhere, and `ToolBox._bash` ran `subprocess` with no
network restriction at all. A contract declaring `fs:worktree, net:none, proc:bash` could
`curl https://api.github.com` and get `HTTP:200` with `denied=False`.

That is worse than having no capability. `net:none` is a claim the contract makes to the
executive and to the operator, and an unenforced claim manufactures confidence rather than
containment. The B2 suite tested filesystem escapes thoroughly and network not at all,
which is how it survived.

These tests are the network half of B2's containment property: denial must come from the
kernel and be visible, NOT from the worker's good manners.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from adapters.tools import NET_SANDBOX_AVAILABLE, ToolBox, ToolCall
from kernel.capabilities import Registry, parse_contract_capabilities
from kernel.deps import Deps

pytestmark = pytest.mark.skipif(
    not NET_SANDBOX_AVAILABLE,
    reason="network containment needs sandbox-exec (macOS); this platform cannot enforce it",
)

REACH = ('curl -s -o /dev/null -w "%{http_code}" --max-time 15 https://api.github.com')


def _box(tmp_path: Path, caps: list[str]) -> ToolBox:
    wt = tmp_path / "wt"
    wt.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    reg = Registry(Deps.deterministic())
    cap = parse_contract_capabilities(reg, "WR", caps, wt)
    return ToolBox(reg, cap.token, wt)


# ------------------------------------------------------------------ denial ----
def test_net_none_blocks_bash_from_reaching_the_network(tmp_path):
    """The exact case that was silently allowed before this file existed."""
    tb = _box(tmp_path, ["fs:worktree", "net:none", "proc:bash"])
    r = tb.dispatch(ToolCall("bash", {"command": REACH}))
    assert "200" not in r.output, f"net:none contract reached the network: {r.output[:200]}"


def test_denial_is_enforcement_not_politeness(tmp_path):
    """A worker cannot opt out by using a different client. Nothing parses the command,
    so every network path in the process is closed, not just the one we thought of."""
    tb = _box(tmp_path, ["fs:worktree", "net:none", "proc:bash"])
    for cmd in (
        REACH,
        "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://api.github.com',timeout=10).status)\"",
        "nc -z -w 5 api.github.com 443 && echo OPEN",
    ):
        r = tb.dispatch(ToolCall("bash", {"command": cmd}))
        assert "200" not in r.output and "OPEN" not in r.output, f"escaped via: {cmd}"


# ---------------------------------------------------------------- no regress --
def test_local_work_still_functions_under_the_sandbox(tmp_path):
    """Containment that breaks the worker is not containment, it is an outage."""
    tb = _box(tmp_path, ["fs:worktree", "net:none", "proc:bash"])
    r = tb.dispatch(ToolCall("bash", {"command": "echo hello > f.txt && cat f.txt"}))
    assert r.ok and "hello" in r.output
    r = tb.dispatch(ToolCall("bash", {"command": "git status --porcelain -uall"}))
    assert r.ok, f"git broke under the network sandbox: {r.output[:200]}"


def test_exit_codes_survive_the_wrapper(tmp_path):
    tb = _box(tmp_path, ["fs:worktree", "net:none", "proc:bash"])
    assert tb.dispatch(ToolCall("bash", {"command": "true"})).ok
    assert not tb.dispatch(ToolCall("bash", {"command": "exit 7"})).ok


# ------------------------------------------------------------------- grant ----
def test_allowlist_grants_network(tmp_path):
    """A contract that asks for network gets it. This is also the documented LIMIT:
    the grant is all-or-nothing. sandbox-exec cannot filter by host, so an allowlist
    naming two hosts opens every host. The limitation is asserted here rather than
    left as a comment nobody reads -- see ToolBox._bash."""
    tb = _box(tmp_path, ["fs:worktree", "net:allowlist:api.github.com", "proc:bash"])
    r = tb.dispatch(ToolCall("bash", {"command": REACH}))
    assert "200" in r.output, "an allowlisted contract could not reach its own host"

    # The honest part: a host NOT on the allowlist is reachable too.
    r2 = tb.dispatch(ToolCall(
        "bash",
        {"command": 'curl -s -o /dev/null -w "%{http_code}" --max-time 15 https://example.com'}))
    assert "200" in r2.output, (
        "if this now fails, per-host filtering was implemented and the docstring, "
        "facts.md, and this test all need updating to match"
    )


def test_capability_is_actually_consulted(tmp_path):
    """The bug was that nothing read the capability. Assert the read happens by checking
    the two contracts produce different behaviour from the same command."""
    denied = _box(tmp_path / "a", ["fs:worktree", "net:none", "proc:bash"])
    granted = _box(tmp_path / "b", ["fs:worktree", "net:allowlist:*", "proc:bash"])
    a = denied.dispatch(ToolCall("bash", {"command": REACH})).output
    b = granted.dispatch(ToolCall("bash", {"command": REACH})).output
    assert ("200" in b) and ("200" not in a)
