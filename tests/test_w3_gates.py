"""The two gates W3 makes urgent.

Neither is about dsh working. Both are about what must NOT become possible once a lane
exists that can reach four labs and, one composition away, author its own tools.
"""

from __future__ import annotations

import pytest

from loop.ratchet import GateProbe, Ratchet, protected_prefixes, touches_protected
from loop.ws_gate import WsGateClosed, require_ws_gate, ws_gate_status


# ============================================ the sovereign lane stays shut ====
def test_ws_gate_refuses():
    """WS (dsh + tool-cordis, the executive authoring its own tools) is gated on a
    verification stack that can judge a self-authored tool. B0 failed and V2 is dropped,
    so that stack does not exist. The gate must REFUSE, not warn."""
    with pytest.raises(WsGateClosed):
        require_ws_gate()


def test_ws_gate_names_every_unmet_condition():
    """A gate that says only 'no' teaches nothing. It must say what would have to be
    true -- and must not imply that the operator can simply switch it on."""
    status = ws_gate_status()
    assert status["open"] is False
    unmet = [c for c in status["conditions"] if not c["met"]]
    assert len(unmet) >= 2, "B0's verdict and the missing review stack are both unmet"
    assert any("B0" in c["name"] for c in unmet)
    assert all(c["why"].strip() for c in status["conditions"])


def test_ws_gate_cannot_be_opened_by_an_env_var():
    """The failure mode this exists to stop is a future session exporting a flag to
    'just try WS'. There must be no such flag.

    Asserted over the AST, not the source text: the module's prose explains at length
    why there is no override, and a substring check would fire on the explanation.
    """
    import ast
    import inspect

    import loop.ws_gate as g
    tree = ast.parse(inspect.getsource(g))

    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "os" not in imports, "the WS gate must not be able to read the environment"

    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not ({"environ", "getenv"} & names), (
        "the WS gate must not consult the environment: an env var is exactly the "
        "self-authorized override the ratchet exists to prevent"
    )

    # ...and no caller-supplied escape hatch either.
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        assert not ({"force", "override", "allow"} & set(args)), (
            f"{fn.name}() takes an override argument; a gate with a bypass is not a gate"
        )


# ================================== the loop cannot propose gate changes ====
def test_protected_prefixes_come_from_the_one_file_git_also_reads():
    """A second copy of the protected list is a second thing to forget to update."""
    prefixes = protected_prefixes()
    assert "kernel/" in prefixes and "ops/" in prefixes and "evals/" in prefixes
    assert not any(p.startswith("#") for p in prefixes)


@pytest.mark.parametrize("path", [
    "kernel/screen.py",
    "ops/protected_paths.txt",
    "verify/seeded/defect_01.py",
    "evals/heldout/task.yaml",
    "executive/schema/decision.schema.json",
])
def test_proposals_touching_the_write_set_are_refused(tmp_path, path):
    r = Ratchet(tmp_path / "staging.yaml")
    with pytest.raises(GateProbe) as e:
        r.propose("tool", "widen_screen", "the screen is too strict",
                  proposed_by="d_1", targets=[path])
    assert path in str(e.value)


def test_a_refused_proposal_is_not_stored_as_pending(tmp_path):
    r = Ratchet(tmp_path / "staging.yaml")
    with pytest.raises(GateProbe):
        r.propose("tool", "x", "why", proposed_by="d_1", targets=["kernel/bus.py"])
    assert r.pending == [], "a refused gate probe must not sit in the queue awaiting a yes"


def test_gate_probes_are_counted_and_survive_a_save(tmp_path):
    """One probe is noise. A loop that keeps proposing gate changes is a finding, and it
    is only a finding if the attempts are counted somewhere the operator will look."""
    p = tmp_path / "staging.yaml"
    r = Ratchet(p)
    for i in range(3):
        with pytest.raises(GateProbe):
            r.propose("tool", f"probe_{i}", "why", proposed_by=f"d_{i}",
                      targets=["kernel/bus.py"])
    r.save()
    assert len(Ratchet(p).gate_probes) == 3


def test_ordinary_proposals_still_pass(tmp_path):
    r = Ratchet(tmp_path / "staging.yaml")
    p = r.propose("tool", "summarize_diff", "recurring manual step",
                  proposed_by="d_9", targets=["adapters/tools/summarize.py"])
    assert p.status == "pending"
    assert r.pending == [p]


def test_targetless_proposals_are_unchanged(tmp_path):
    """Back-compat: every proposal written before targets existed still loads and works."""
    r = Ratchet(tmp_path / "staging.yaml")
    assert r.propose("skill", "s", "why", proposed_by="d_1").status == "pending"


def test_escape_shaped_targets_are_caught(tmp_path):
    """`kernel/../kernel/bus.py` and `./kernel/bus.py` are the same file as `kernel/bus.py`;
    a prefix check on the raw string would miss both."""
    r = Ratchet(tmp_path / "staging.yaml")
    for target in ("./kernel/bus.py", "loop/../kernel/bus.py", "kernel//bus.py"):
        with pytest.raises(GateProbe):
            r.propose("tool", "x", "why", proposed_by="d_1", targets=[target])


def test_touches_protected_reports_the_matching_prefix():
    hits = touches_protected(["kernel/bus.py", "adapters/base.py"])
    assert hits == [("kernel/bus.py", "kernel/")]
