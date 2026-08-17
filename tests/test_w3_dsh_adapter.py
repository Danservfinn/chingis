"""W3 — the dsh lane. B2 acceptance: the same contract runs on any lane unmodified.

These tests drive the REAL subprocess path against a fake `dsh` executable rather than
mocking the adapter's internals, for the same reason the W2 tests do: what breaks in a
CLI-driven lane is the command line, the environment, and the cwd -- exactly the things
a mock would paper over. The fake asserts on argv and env and can be told to refuse,
hang, or crash.

The live counterpart is `tests/live_w3_smoke.py`, which is skipped without a key.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from adapters.base import Result, Status
from adapters.dsh_adapter import PROVIDER_KEY_ENV, DshAdapter

CONTRACT = {
    "contract_id": "c_0001",
    "lane": "W3",
    "objective": "Add a docstring to hello().",
    "context_refs": ["src/**"],
    "capabilities": ["fs:worktree", "net:none", "proc:bash"],
    "output_schema": "diff+summary",
    "budget": {"wall_min": 5, "quota_units": 1, "tokens_usd": 0.05,
               "tool_calls": 8, "inlane_retries": 0},
    "verification": ["V1:pytest"],
}


@pytest.fixture
def worktree(tmp_path):
    """A git worktree standing in for the blast radius the runtime owns."""
    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "hello.py").write_text("def hello():\n    return 'hi'\n")
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=wt, check=True)
    return wt


def _fake_dsh(tmp_path: Path, body: str, name: str = "dsh") -> Path:
    """Write an executable stub that stands in for the dsh CLI."""
    p = tmp_path / name
    p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    p.chmod(0o755)
    return p


@pytest.fixture
def adapter_factory(tmp_path):
    def make(body: str, **kw):
        bin_ = _fake_dsh(tmp_path, body)
        kw.setdefault("key_env_override", {"zai": "FAKE_W3_KEY"})
        os.environ["FAKE_W3_KEY"] = "x" * 20
        return DshAdapter(dsh_bin=str(bin_), dsh_home=tmp_path / "dshhome", **kw)
    yield make
    os.environ.pop("FAKE_W3_KEY", None)


# ------------------------------------------------------------------ shape ----
def test_lane_and_result_shape(adapter_factory, worktree):
    a = adapter_factory('echo "made the edit"\n')
    r = a.run(CONTRACT, worktree)
    assert isinstance(r, Result)
    assert r.lane == "W3"
    assert r.model == "zai/glm-5.2", "the lane records provider/model, not model alone"
    assert r.status is Status.DONE


def test_diff_is_the_artifact(adapter_factory, worktree):
    a = adapter_factory('printf "def hello():\\n    \\"\\"\\"Hi.\\"\\"\\"\\n" > src/hello.py\n'
                        'echo "added docstring"\n')
    r = a.run(CONTRACT, worktree)
    assert "Hi." in r.artifacts["diff"]
    assert r.status is Status.DONE


def test_contract_model_overrides_default(adapter_factory, worktree):
    a = adapter_factory('echo ok\n')
    c = {**CONTRACT, "model": "anthropic/claude-sonnet-4-5"}
    os.environ["ANTHROPIC_API_KEY"] = "y" * 20
    try:
        r = a.run(c, worktree)
        assert r.model == "anthropic/claude-sonnet-4-5"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


# ------------------------------------------------------- command + env ----
def test_invocation_is_worktree_scoped_and_patched(adapter_factory, worktree):
    """cwd is the worktree (dsh roots its own sandbox at process cwd) and our
    composition patch is applied. Both are containment-relevant, so both are asserted."""
    a = adapter_factory('echo "cwd=$PWD" > "$FAKE_LOG"\n'
                        'echo "args=$*" >> "$FAKE_LOG"\n'
                        'echo done\n')
    log = worktree.parent / "invocation.log"
    os.environ["FAKE_LOG"] = str(log)
    try:
        a.run(CONTRACT, worktree)
    finally:
        os.environ.pop("FAKE_LOG", None)
    text = log.read_text()
    assert f"cwd={worktree.resolve()}" in text
    assert "--profile headless" in text
    assert "w3.cordis.yml" in text, "the W3 composition patch must be applied"


def test_session_state_never_lands_in_the_worktree(adapter_factory, worktree, tmp_path):
    """dsh session logs are flywheel data, not artifact. A session file inside the
    worktree would ride into the diff and be reviewed as part of the change."""
    a = adapter_factory('echo ok\n')
    a.run(CONTRACT, worktree)
    stray = [p for p in worktree.rglob("*") if "dsh" in p.name.lower()]
    assert not stray, f"dsh state leaked into the worktree: {stray}"
    assert str(a.dsh_home).startswith(str(tmp_path))


# ------------------------------------------------------------- failures ----
def test_missing_credential_refused_before_launch(adapter_factory, worktree):
    """A route whose key is unset must fail Chingis-side, naming the lane and the
    reference -- not as an opaque worker failure carrying dsh's own prose."""
    a = adapter_factory('echo "SHOULD NOT RUN" > "$FAKE_LOG"\n',
                        key_env_override={"zai": "DEFINITELY_UNSET_KEY_W3"})
    os.environ.pop("DEFINITELY_UNSET_KEY_W3", None)
    r = a.run(CONTRACT, worktree)
    assert r.status is Status.FAILED
    assert "DEFINITELY_UNSET_KEY_W3" in r.artifacts["error"]
    assert "zai" in r.artifacts["error"]


def test_refusal_detected_from_final_text(adapter_factory, worktree):
    a = adapter_factory('echo "I cannot help with that request."\n')
    r = a.run(CONTRACT, worktree)
    assert r.status is Status.REFUSED
    assert r.refusal_signal


def test_nonzero_exit_is_failed_not_raised(adapter_factory, worktree):
    a = adapter_factory('echo "boom" >&2\nexit 3\n')
    r = a.run(CONTRACT, worktree)
    assert r.status is Status.FAILED
    assert isinstance(r, Result), "an adapter crash is a worker_failed, never our exception"


def test_wall_budget_enforced_as_timeout(adapter_factory, worktree):
    a = adapter_factory('sleep 30\n')
    c = {**CONTRACT, "budget": {**CONTRACT["budget"], "wall_min": 0.02}}  # 1.2s
    r = a.run(c, worktree)
    assert r.status is Status.TIMEOUT
    assert r.raw_cost["wall_s"] < 20, "the budget must kill the worker, not wait it out"


def test_missing_binary_is_failed_not_traceback(worktree, tmp_path):
    a = DshAdapter(dsh_bin=str(tmp_path / "nope"), dsh_home=tmp_path / "h",
                   key_env_override={"zai": "FAKE_W3_KEY"})
    os.environ["FAKE_W3_KEY"] = "x" * 20
    try:
        r = a.run(CONTRACT, worktree)
    finally:
        os.environ.pop("FAKE_W3_KEY", None)
    assert r.status is Status.FAILED


# ------------------------------------------------------------ contract ----
def test_provider_key_map_matches_the_composition():
    """The adapter's fail-fast map and the cordis patch must name the same env refs;
    a drift here means the adapter clears a route dsh will then refuse (or vice versa)."""
    import re
    yml = (Path(__file__).resolve().parent.parent / "fleets" / "dsh" / "w3.cordis.yml").read_text()
    declared = dict(re.findall(r"^      ([a-z-]+):\n        apiKeyEnv: (\w+)$", yml, re.M))
    assert declared, "no provider routes parsed out of w3.cordis.yml"
    assert declared == PROVIDER_KEY_ENV


def test_healthcheck_reports_missing_binary(tmp_path):
    a = DshAdapter(dsh_bin=str(tmp_path / "nope"), dsh_home=tmp_path / "h")
    ok, msg = a.healthcheck()
    assert not ok and "not" in msg.lower()
