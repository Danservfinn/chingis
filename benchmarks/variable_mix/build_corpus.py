#!/usr/bin/env python3
"""Build the VARIABLE-mix corpus and its fixture repo. Spec §12.

    ./benchmarks/variable_mix/build_corpus.py          # writes fixture/ and contracts/

M0's pre-registered claim is about the **variable** mix, over >=60 contracts, on
success-per-resource. It also pre-registers that Chingis is expected to LOSE the routine
mix -- and the existing corpus (benchmarks/b0/contracts) is 21 routine "create this file"
tasks, which is why the first founding run tied 4/4 and said nothing.

A fixed harness loses where **judgment between attempts** pays: where the first approach
can be wrong, where a task should be refused or rerouted, where the requirement is
ambiguous enough that interpretation differs, where a cheap lane suffices and an
expensive one is waste. This corpus varies along those axes on purpose, and each contract
declares which axis it exercises so a result can be read per-axis rather than pooled into
one uninformative number.

The fixture repo is GENERATED, not committed: a corpus whose target you cannot rebuild
byte-for-byte is not reproducible, and B0's contracts pointed at a personal project that
no one else can obtain.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
CONTRACTS = HERE / "contracts"

#: The fixture: a small library with real, differing failure modes to work against.
FILES: dict[str, str] = {
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n",
    "src/__init__.py": "",
    "src/stats.py": (
        "def mean(xs):\n"
        '    """Arithmetic mean."""\n'
        "    return sum(xs) / len(xs)\n\n\n"
        "def median(xs):\n"
        '    """Median. BUG: wrong for even-length input."""\n'
        "    s = sorted(xs)\n"
        "    return s[len(s) // 2]\n"
    ),
    "src/parsing.py": (
        "import json\n\n\n"
        "def load_config(text):\n"
        '    """Parse config text. BUG: swallows the error and returns None."""\n'
        "    try:\n"
        "        return json.loads(text)\n"
        "    except ValueError:\n"
        "        return None\n"
    ),
    "src/cache.py": (
        "_STORE = {}\n\n\n"
        "def put(key, value, ttl=None):\n"
        '    """Store a value. BUG: ttl is accepted and silently ignored."""\n'
        "    _STORE[key] = value\n"
        "    return value\n\n\n"
        "def get(key):\n"
        "    return _STORE.get(key)\n"
    ),
    "src/paths.py": (
        "import os\n\n\n"
        "def join_under(root, *parts):\n"
        '    """Join parts under root. BUG: does not prevent escaping root."""\n'
        "    return os.path.join(root, *parts)\n"
    ),
    "tests/__init__.py": "",
    "tests/test_stats.py": (
        "from src.stats import mean, median\n\n\n"
        "def test_mean():\n"
        "    assert mean([1, 2, 3]) == 2\n\n\n"
        "def test_median_odd():\n"
        "    assert median([3, 1, 2]) == 2\n"
    ),
}

#: axis -> why a fixed harness is expected to underperform on it.
AXES = {
    "retry_pays": "the first attempt can plausibly be wrong; a second, differently-framed "
                  "attempt is what succeeds. M0 retries identically, once.",
    "ambiguous": "the requirement is underspecified, so interpretation differs and the "
                 "verifier is the only arbiter.",
    "refusal_adjacent": "security-flavoured phrasing that some fleets decline. A fixed "
                        "lane has nowhere to go; routing does.",
    "cheap_suffices": "trivial work where an expensive lane is pure waste. Tests whether "
                      "adaptive routing spends less for the same result.",
    "multi_file": "the change spans files, so scope discipline and diff_scope matter.",
}

TASKS: list[tuple[str, str, str, list[str]]] = [
    # (id_suffix, axis, objective, context_refs)
    ("median_even", "retry_pays",
     "src/stats.py median() returns the wrong value for even-length input; it must average "
     "the two middle values. Fix it and add a test proving the even-length case. "
     "Do not modify existing tests.", ["src/**", "tests/**"]),
    ("parse_reraise", "retry_pays",
     "src/parsing.py load_config() swallows malformed JSON and returns None, hiding the "
     "error from callers. Make it raise a clear error naming the problem instead, and add "
     "a test. Do not modify existing tests.", ["src/**", "tests/**"]),
    ("cache_ttl", "ambiguous",
     "src/cache.py accepts a ttl argument and silently ignores it. Make ttl actually work. "
     "Add tests covering expiry.", ["src/**", "tests/**"]),
    ("path_escape", "refusal_adjacent",
     "src/paths.py join_under() lets a caller escape the root directory with '..' segments, "
     "which is a path-traversal vulnerability. Harden it so escaping the root is impossible, "
     "and add tests for the attack cases.", ["src/**", "tests/**"]),
    ("secret_scan", "refusal_adjacent",
     "Add src/secrets_check.py with scan_text(text) returning a list of substrings that look "
     "like leaked API credentials, plus tests. This is a defensive scanner for a CI hook.",
     ["src/**", "tests/**"]),
    ("mean_empty", "cheap_suffices",
     "src/stats.py mean() raises ZeroDivisionError on an empty list. Make it raise a clear "
     "ValueError naming the problem. Add one test.", ["src/**", "tests/**"]),
    ("shared_helper", "multi_file",
     "src/stats.py and src/parsing.py both need a shared _require_non_empty(value, name) "
     "helper that raises ValueError. Add it in a new src/validation.py, use it in BOTH "
     "modules, and add tests for the helper.", ["src/**", "tests/**"]),
    ("median_docs", "ambiguous",
     "The median() docstring in src/stats.py describes behaviour the code does not have. "
     "Make the code and the documentation agree. Add a test for whichever behaviour you "
     "conclude is correct.", ["src/**", "tests/**"]),
]

BUDGET = {"wall_min": 8, "quota_units": 1, "tokens_usd": 0.50,
          "tool_calls": 25, "inlane_retries": 0}


def build_fixture() -> Path:
    subprocess.run(["rm", "-rf", str(FIXTURE)], check=True)
    for rel, content in FILES.items():
        p = FIXTURE / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=FIXTURE, check=True)
    subprocess.run(["git", "add", "-A"], cwd=FIXTURE, check=True)
    subprocess.run(["git", "-c", "user.email=vm@bench", "-c", "user.name=vm",
                    "commit", "-qm", "fixture"], cwd=FIXTURE, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=FIXTURE, check=True)
    return FIXTURE


def build_contracts() -> int:
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    for old in CONTRACTS.glob("v_*.json"):
        old.unlink()
    for i, (suffix, axis, objective, refs) in enumerate(TASKS, 1):
        cid = f"v_{i:04d}_{suffix}"
        (CONTRACTS / f"{cid}.json").write_text(json.dumps({
            "contract_id": cid,
            "repo": str(FIXTURE),
            "base_ref": "main",
            "objective": objective,
            "context_refs": refs,
            "axis": axis,
            "axis_rationale": AXES[axis],
            "v1_command": "pytest",
            "budget": dict(BUDGET),
        }, indent=2) + "\n")
    return len(TASKS)


def main() -> int:
    build_fixture()
    n = build_contracts()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=FIXTURE,
                       capture_output=True, text=True)
    print(f"fixture:   {FIXTURE}")
    print(f"contracts: {n} in {CONTRACTS}")
    print(f"baseline pytest on the untouched fixture: "
          f"{'PASS' if p.returncode == 0 else 'fails as expected'}")
    by_axis: dict[str, int] = {}
    for _, axis, _, _ in TASKS:
        by_axis[axis] = by_axis.get(axis, 0) + 1
    for axis, count in sorted(by_axis.items()):
        print(f"  {axis:<18} {count}")
    print(f"\n{n} contracts. The pre-registered bar is >=60 on the variable mix, so this "
          f"is a SEED, not the corpus:\nit exercises every axis at n>=1 and must be grown "
          f"before any founding claim is read from it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
