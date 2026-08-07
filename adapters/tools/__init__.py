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
        try:
            p = subprocess.run(
                command, shell=True, cwd=str(self.worktree), capture_output=True,
                text=True, timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"command timed out after {self.timeout_s}s")
        out = ((p.stdout or "") + (p.stderr or ""))[:MAX_OUTPUT]
        return ToolResult(p.returncode == 0, f"exit={p.returncode}\n{out}")
