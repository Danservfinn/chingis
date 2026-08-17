"""The WR lane's tool loop: bash, read_file, apply_patch.

Every call is capability-gated and worktree-scoped. Containment is enforced here, in code,
by `Registry.check_*` and `safe_join` -- never by the model's willingness to stay put.

B2 acceptance: an adversarial contract attempting to read outside the worktree must be
denied by the capability check, not by model politeness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.capabilities import CapabilityDenied, Registry, safe_join

MAX_OUTPUT = 20_000

#: Deny every network operation, allow everything else. Path containment is already the
#: worktree cwd plus the token's path scope, so this profile deliberately changes nothing
#: except the network -- a broader profile would start breaking git and pytest, and a
#: containment that breaks the worker is an outage rather than a boundary.
_NO_NET_PROFILE = "(version 1)(allow default)(deny network*)"

#: Whether this platform can enforce `net:none` at all. False makes `_bash` fail closed.
NET_SANDBOX_AVAILABLE = Path("/usr/bin/sandbox-exec").exists()


class NetContainmentUnavailable(RuntimeError):
    """Raised when a `net:none` contract asks for bash on a platform that cannot contain
    it. Deliberately not caught anywhere: running unconstrained under a contract that
    declares no network is the bug this class exists to prevent from recurring."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    ok: bool
    output: str
    denied: bool = False


#: Tool schemas handed to the model. Kept minimal on purpose -- the executive specifies
#: sequencing in the contract; there is no inner harness making choices here.
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the worktree.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the worktree root."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": "Write full file contents inside the worktree, creating parent dirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the worktree root."},
                    "content": {"type": "string", "description": "Complete new file contents."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command with the worktree as cwd.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]


class ToolBox:
    """Kernel-gated execution of a worker's tool calls."""

    def __init__(self, registry: Registry, token: str, worktree: Path, *, timeout_s: int = 120) -> None:
        self.registry, self.token = registry, token
        self.worktree = Path(worktree).resolve()
        self.timeout_s = timeout_s
        self.calls: list[dict] = []

    def dispatch(self, call: ToolCall) -> ToolResult:
        """Every tool call goes through here. Denials become results, not crashes -- the
        worker must see that it was refused so it can adapt, and the denial is logged."""
        try:
            self.registry.check_tool(self.token, call.name)
            fn = getattr(self, f"_{call.name}", None)
            if fn is None:
                return ToolResult(False, f"unknown tool {call.name!r}")
            result = fn(**call.args)
        except CapabilityDenied as e:
            result = ToolResult(False, f"DENIED by capability check: {e}", denied=True)
        except TypeError as e:
            result = ToolResult(False, f"bad arguments for {call.name}: {e}")
        self.calls.append({"tool": call.name, "args": call.args,
                           "ok": result.ok, "denied": result.denied})
        return result

    # ------------------------------------------------------------------ tools --
    def _read_file(self, path: str) -> ToolResult:
        target = safe_join(self.worktree, path)          # raises CapabilityDenied on escape
        self.registry.check_path(self.token, target)
        if not target.is_file():
            return ToolResult(False, f"no such file: {path}")
        return ToolResult(True, target.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT])

    def _apply_patch(self, path: str, content: str) -> ToolResult:
        target = safe_join(self.worktree, path)
        self.registry.check_path(self.token, target, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"wrote {path} ({len(content)} bytes)")

    def _bash(self, command: str) -> ToolResult:
        # cwd is the worktree. The sandbox is the capability check plus this cwd; we do not
        # attempt to parse the command for escapes, because parsing shell is a losing game.
        # Path containment for reads/writes is enforced by the OS working directory and by
        # the fact that this token grants no path outside the worktree to the other tools.
        #
        # NETWORK is enforced by the OS for the same reason: a worker can reach the network
        # with curl, python, nc, or something nobody listed, so the boundary has to sit
        # below the command rather than in a pattern match on it. Until 2026-08-17 there
        # was no boundary at all -- `net:none` was declared by every contract and enforced
        # by nothing, which is a claim that manufactures confidence instead of containment.
        argv, shell = self._wrap_for_net(command)
        try:
            p = subprocess.run(
                argv, shell=shell, cwd=str(self.worktree), capture_output=True,
                text=True, timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"command timed out after {self.timeout_s}s")
        out = ((p.stdout or "") + (p.stderr or ""))[:MAX_OUTPUT]
        return ToolResult(p.returncode == 0, f"exit={p.returncode}\n{out}")

    # ------------------------------------------------------------- network --
    def _wrap_for_net(self, command: str):
        """Return (argv-or-command, shell_flag), sandboxed iff the token grants no network.

        `sandbox-exec` is the enforcement point. It is deprecated by Apple and still the
        only thing on this machine that can deny a whole process tree the network without
        root, a VM, or a per-command allowlist nobody can maintain. If it disappears in a
        future macOS, `NET_SANDBOX_AVAILABLE` goes False and this **fails closed** -- a
        `net:none` contract stops running bash rather than running it unconstrained,
        because silently reverting to the old behaviour is exactly the failure this
        replaced.

        KNOWN LIMIT, asserted by `tests/test_net_containment.py`: the grant is
        all-or-nothing. `sandbox-exec` filters by operation, not by host, so
        `net:allowlist:a.example` opens **every** host, not just that one. The contract
        schema is more expressive than the enforcement, so a per-host allowlist reads as
        a stricter promise than the kernel can keep. Treat any non-empty allowlist as
        "this contract has the internet", and see policy/facts.md.
        """
        cap = self.registry.get(self.token)
        if cap.net:                                   # any grant => unrestricted, per above
            return command, True
        if not NET_SANDBOX_AVAILABLE:
            raise NetContainmentUnavailable(
                "net:none cannot be enforced on this platform (sandbox-exec absent); "
                "refusing to run bash unconstrained under a contract that declares no "
                "network"
            )
        return ["/usr/bin/sandbox-exec", "-p", _NO_NET_PROFILE, "/bin/bash", "-c",
                command], False
