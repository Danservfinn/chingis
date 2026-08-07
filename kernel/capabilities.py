"""Capability tokens. Spec §2 invariants.

Every tool and adapter call carries a token scoped to (worker, tool, path-prefix, network).
No token, no call.

The executive cannot mint, widen, or transfer capabilities -- including for itself. That is
enforced structurally: minting requires a `Registry`, and the executive is never handed one.
It receives token ids, which are opaque and unforgeable-by-guessing.

The kernel never asks a model whether a permission check should pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .deps import Deps


class CapabilityDenied(Exception):
    """Raised on any denied access. Never caught inside the kernel -- it propagates to a
    denial event, because a swallowed capability error is indistinguishable from success."""


@dataclass(frozen=True, slots=True)
class Capability:
    token: str
    worker: str
    tools: frozenset[str]
    path_prefix: Path | None       # None => no filesystem access at all
    net: frozenset[str]            # empty => no network; {"*"} => unrestricted

    def allows_tool(self, tool: str) -> bool:
        return tool in self.tools

    def allows_path(self, path: str | Path) -> bool:
        if self.path_prefix is None:
            return False
        try:
            # resolve() collapses .. and follows symlinks: a worktree-scoped token must not
            # be escapable with ../../ or a symlink planted inside the worktree.
            target = Path(path).resolve()
            root = self.path_prefix.resolve()
        except (OSError, RuntimeError):
            return False
        return target == root or root in target.parents

    def allows_net(self, host: str) -> bool:
        return "*" in self.net or host in self.net


class Registry:
    """Mints and checks capabilities. Kernel-only -- never handed to a model."""

    def __init__(self, deps: Deps | None = None) -> None:
        self._deps = deps or Deps()
        self._caps: dict[str, Capability] = {}
        self._denials: list[dict] = []

    def mint(
        self,
        worker: str,
        tools: set[str] | frozenset[str],
        path_prefix: Path | str | None,
        net: set[str] | frozenset[str] | None = None,
    ) -> Capability:
        cap = Capability(
            token=self._deps.ids.next_id("cap"),
            worker=worker,
            tools=frozenset(tools),
            path_prefix=Path(path_prefix) if path_prefix is not None else None,
            net=frozenset(net or ()),
        )
        self._caps[cap.token] = cap
        return cap

    def get(self, token: str) -> Capability:
        if token not in self._caps:
            raise CapabilityDenied(f"unknown capability token: {token!r}")
        return self._caps[token]

    def revoke(self, token: str) -> None:
        self._caps.pop(token, None)

    @property
    def denials(self) -> list[dict]:
        """Every denial, for the audit log. Denials are data, not just exceptions."""
        return list(self._denials)

    def _deny(self, token: str, kind: str, detail: str) -> None:
        self._denials.append({"token": token, "kind": kind, "detail": detail})
        raise CapabilityDenied(f"{kind} denied: {detail}")

    # ------------------------------------------------------------------ checks --
    def check_tool(self, token: str, tool: str) -> Capability:
        cap = self.get(token)
        if not cap.allows_tool(tool):
            self._deny(token, "tool", f"{cap.worker} may not call {tool!r}")
        return cap

    def check_path(self, token: str, path: str | Path, *, write: bool = False) -> Capability:
        cap = self.get(token)
        if not cap.allows_path(path):
            self._deny(
                token, "path",
                f"{cap.worker} may not {'write' if write else 'read'} {path!r} "
                f"(scoped to {cap.path_prefix})",
            )
        return cap

    def check_net(self, token: str, host: str) -> Capability:
        cap = self.get(token)
        if not cap.allows_net(host):
            self._deny(token, "net", f"{cap.worker} may not reach {host!r}")
        return cap


def parse_contract_capabilities(
    registry: Registry, worker: str, caps: list[str], worktree: Path
) -> Capability:
    """Turn a contract's `capabilities` list into one minted token.

    Contract syntax (see contracts/contract.schema.json):
        fs:worktree | fs:readonly:<path> | net:none | net:allowlist:<h,h> | proc:bash
    """
    tools: set[str] = set()
    net: set[str] = set()
    path_prefix: Path | None = None

    for c in caps:
        if c == "fs:worktree":
            path_prefix = worktree
            tools |= {"read_file", "apply_patch"}
        elif c.startswith("fs:readonly:"):
            path_prefix = Path(c.split(":", 2)[2])
            tools.add("read_file")
        elif c == "net:none":
            pass
        elif c.startswith("net:allowlist:"):
            net |= {h.strip() for h in c.split(":", 2)[2].split(",") if h.strip()}
        elif c == "proc:bash":
            tools.add("bash")
        else:
            raise ValueError(f"unrecognised capability {c!r}")

    return registry.mint(worker=worker, tools=tools, path_prefix=path_prefix, net=net)


def is_within(path: Path | str, root: Path | str) -> bool:
    """Containment check used by the WR tool loop. Kept here so there is exactly one."""
    try:
        p, r = Path(path).resolve(), Path(root).resolve()
    except (OSError, RuntimeError):
        return False
    return p == r or r in p.parents


def safe_join(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes.

    Absolute paths, .. traversal, and symlinks pointing outside are all rejected here
    rather than by the model's good manners.
    """
    root = Path(root).resolve()
    candidate = (root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
    if not is_within(candidate, root):
        raise CapabilityDenied(f"path {rel!r} escapes the worktree {root}")
    return candidate
