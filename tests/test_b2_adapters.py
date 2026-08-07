"""B2 acceptance.

Plan §5: "One contract runs on any lane unmodified; WR passes worktree-containment tests."

The containment tests are the important half. Spec §4: an adversarial contract attempting
to read outside the worktree must be denied by the capability check, NOT by model politeness.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from adapters.base import Result, Status, Worktree, detect_refusal
from adapters.codex_adapter import CodexAdapter
from adapters.glm_cc_adapter import GlmCodeAdapter
from adapters.local_adapter import LocalAdapter
from adapters.raw_adapter import RawAdapter
from adapters.tools import ToolBox, ToolCall
from kernel.capabilities import Registry, parse_contract_capabilities
from kernel.deps import Deps

CONTRACT = {
    "contract_id": "c_0001",
    "lane": "WR",
    "objective": "Add a docstring to hello().",
    "context_refs": ["src/**"],
    "capabilities": ["fs:worktree", "net:none", "proc:bash"],
    "output_schema": "diff+summary",
    "budget": {"wall_min": 5, "quota_units": 1, "tokens_usd": 0.05,
               "tool_calls": 8, "inlane_retries": 0},
    "verification": ["V1:pytest"],
}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "src" / "hello.py").write_text("def hello():\n    return 'hi'\n")
    (tmp_path / "SECRET.txt").write_text("employer-adjacent data")
    subprocess.run(["git", "init", "-q"], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=r, check=True)
    return r


@pytest.fixture
def box(tmp_path, repo):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "src").mkdir()
    (wt / "src" / "hello.py").write_text("def hello():\n    return 'hi'\n")
    reg = Registry(Deps.deterministic())
    cap = parse_contract_capabilities(reg, "WR", CONTRACT["capabilities"], wt)
    return ToolBox(reg, cap.token, wt), wt


# -------------------------------------------------------------- containment ----
def test_read_inside_worktree_allowed(box):
    tb, _ = box
    r = tb.dispatch(ToolCall("read_file", {"path": "src/hello.py"}))
    assert r.ok and "def hello" in r.output


def test_read_outside_worktree_denied_by_capability(box, tmp_path):
    """The adversarial case. Denial must come from the kernel, and must be visible."""
    tb, _ = box
    for escape in ("../SECRET.txt", "../../SECRET.txt", "/etc/passwd",
                   "src/../../SECRET.txt", str(tmp_path / "SECRET.txt")):
        r = tb.dispatch(ToolCall("read_file", {"path": escape}))
        assert not r.ok, f"{escape} was not denied"
        assert r.denied, f"{escape} failed but was not recorded as a capability denial"
        assert "DENIED" in r.output


def test_write_outside_worktree_denied(box):
    tb, _ = box
    r = tb.dispatch(ToolCall("apply_patch", {"path": "../escaped.py", "content": "x=1"}))
    assert not r.ok and r.denied


def test_symlink_escape_denied(box):
    tb, wt = box
    (wt / "link").symlink_to(wt.parent / "SECRET.txt")
    r = tb.dispatch(ToolCall("read_file", {"path": "link"}))
    assert not r.ok and r.denied, "a symlink planted inside the worktree must not escape it"


def test_tool_not_granted_is_denied(tmp_path):
    """net:none + no proc:bash => bash is unavailable, regardless of what the model asks."""
    wt = tmp_path / "wt2"
    wt.mkdir()
    reg = Registry(Deps.deterministic())
    cap = parse_contract_capabilities(reg, "WR", ["fs:worktree", "net:none"], wt)
    tb = ToolBox(reg, cap.token, wt)
    r = tb.dispatch(ToolCall("bash", {"command": "echo hi"}))
    assert not r.ok and r.denied


def test_every_call_is_logged_including_denials(box):
    tb, _ = box
    tb.dispatch(ToolCall("read_file", {"path": "src/hello.py"}))
    tb.dispatch(ToolCall("read_file", {"path": "../SECRET.txt"}))
    assert len(tb.calls) == 2
    assert [c["denied"] for c in tb.calls] == [False, True]


def test_apply_patch_writes_inside(box):
    tb, wt = box
    r = tb.dispatch(ToolCall("apply_patch", {"path": "src/new.py", "content": "x = 1\n"}))
    assert r.ok and (wt / "src" / "new.py").read_text() == "x = 1\n"


# ------------------------------------------------------- one interface, all lanes ----
def test_every_lane_implements_the_interface():
    lanes = [RawAdapter(Registry(Deps.deterministic())), CodexAdapter(),
             GlmCodeAdapter(), LocalAdapter()]
    assert {a.lane for a in lanes} == {"WR", "W1", "W2", "W0"}
    for a in lanes:
        assert callable(a.healthcheck) and callable(a.run)
        ok, msg = a.healthcheck()
        assert isinstance(ok, bool) and isinstance(msg, str)


def test_the_same_contract_dict_is_accepted_by_every_lane():
    """B2's literal criterion: one contract, unmodified, across lanes."""
    import inspect
    for cls in (RawAdapter, CodexAdapter, GlmCodeAdapter, LocalAdapter):
        params = list(inspect.signature(cls.run).parameters)
        assert params[1:] == ["contract", "worktree"], f"{cls.__name__} has a divergent run()"


def test_result_normalizes_across_fleets():
    r = Result(Status.REFUSED, {"diff": ""}, {"wall_s": 1.0}, refusal_signal="x", lane="W1")
    d = r.to_dict()
    assert set(d) == {"status", "artifacts", "raw_cost", "refusal_signal", "lane", "model"}
    assert d["status"] == "refused"


# ------------------------------------------------------------------ refusals ----
@pytest.mark.parametrize("text", [
    "I can't help with that request.",
    "I'm unable to assist with this.",
    "This violates our usage policies.",
    "I must decline to write that.",
])
def test_refusal_phrases_detected(text):
    assert detect_refusal(text, diff_empty=True) is not None


def test_empty_diff_with_long_explanation_is_a_refusal():
    """The shape matters as much as the phrasing: prose instead of a patch is a refusal
    in effect, whatever the model called it."""
    assert detect_refusal("Here is a lengthy discussion. " * 20, diff_empty=True) == \
        "empty_diff_with_explanation"


def test_normal_summary_with_a_diff_is_not_a_refusal():
    assert detect_refusal("Added the docstring to hello().", diff_empty=False) is None


# ----------------------------------------------------------------- worktrees ----
def test_worktree_is_disposable(repo, tmp_path):
    wt_path = tmp_path / "wt-disposable"
    with Worktree(repo, "HEAD", wt_path) as wt:
        assert (wt / "src" / "hello.py").exists()
        (wt / "src" / "hello.py").write_text("def hello():\n    '''hi'''\n    return 'hi'\n")
        diff = Worktree(repo, "HEAD", wt_path).diff()
        assert "'''hi'''" in diff
    assert not wt_path.exists(), "the worktree is the blast radius; it must not survive"


# ------------------------------------------------- artifact hygiene (W1/W2) ----
def test_tooling_artifacts_are_stripped_from_the_diff(tmp_path):
    """The packaged lanes ARE Claude Code, and the operator's own plugins write into the
    worktree. Without this, every W1/W2 artifact carries editor noise the reviewer reads
    as part of the change -- and `src/CLAUDE.md` sits inside a `src/**` context_ref, so
    diff_scope would not even flag it."""
    from adapters.base import strip_tooling_artifacts

    wt = tmp_path / "wt"
    (wt / "src").mkdir(parents=True)
    (wt / "src" / "CLAUDE.md").write_text("<claude-mem-context>\n# Recent Activity\n")
    (wt / "src" / "real.py").write_text("x = 1")
    removed = strip_tooling_artifacts(wt, tracked=set())
    assert removed == ["src/CLAUDE.md"]
    assert not (wt / "src" / "CLAUDE.md").exists()
    assert (wt / "src" / "real.py").exists()


def test_a_contract_may_still_edit_a_real_claude_md(tmp_path):
    """Three conditions must hold before anything is removed. A CLAUDE.md that was in the
    base commit, or that lacks the tool's signature, is the worker's business."""
    from adapters.base import strip_tooling_artifacts

    wt = tmp_path / "wt2"
    wt.mkdir()
    (wt / "CLAUDE.md").write_text("# Project instructions\nRun tests with pytest.\n")
    assert strip_tooling_artifacts(wt, tracked=set()) == [], "no tool signature -> keep"

    (wt / "CLAUDE.md").write_text("<claude-mem-context>\nstuff\n")
    assert strip_tooling_artifacts(wt, tracked={"CLAUDE.md"}) == [], "pre-existing -> keep"


def test_packaged_lanes_grant_edit_permission(tmp_path):
    """Headless Claude Code will not write a file without a permission grant. Without it
    the worker returns an explanation and no diff, which the refusal heuristic then
    reports as a refusal -- a configuration error wearing a safety-refusal costume."""
    import inspect
    import re
    from adapters import claude_adapter, glm_cc_adapter
    for mod in (claude_adapter, glm_cc_adapter):
        src = inspect.getsource(mod)
        assert '"--permission-mode"' in src and '"acceptEdits"' in src
        # Match the ARGUMENT, not the word: the docstring says "acceptEdits, not
        # bypassPermissions", and a substring check on prose would fail on its own comment.
        assert not re.search(r'"bypassPermissions"', src), "the grant must stay scoped"
