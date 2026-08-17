"""The B1H contract set: five small repair tasks, objectively verified.

Design constraints, each chosen to make the comparison mean something:

  * **Objective pass/fail.** Every task ships a test that currently FAILS and must pass.
    No rubric, no judgment, no reviewer — which is what makes this benchmark immune to
    the reviewer-quality problem that sank B0.
  * **Regression-guarded.** Every fixture also ships tests that already pass. A harness
    that "fixes" the target by breaking something else fails.
  * **Test-weakening-guarded.** `diff_scope` flags a diff that edits the test files. The
    cheapest way to pass any of these is to delete the failing assertion, so that route
    is closed and the closure is measured, not trusted.
  * **Small.** One file, one bug, a few minutes. This is a smoke-scale contrast, not a
    capability evaluation, and the tasks are sized so that arm cost stays trivial.

The bugs are deliberately of different KINDS -- arithmetic, boundary, mutable default,
control flow, unicode -- rather than five flavours of the same mistake, because a single
kind would measure one narrow competence and read as a harness difference.
"""

from __future__ import annotations

# Each entry: id, objective, files {path: content}, and the failing test's name.
CONTRACTS: list[dict] = [
    {
        "id": "hc_01_median",
        "objective": (
            "src/stats.py has a bug: median() returns the wrong value for even-length "
            "input. It must average the two middle values. Fix src/stats.py. "
            "Do not modify any test."
        ),
        "files": {
            "src/__init__.py": "",
            "src/stats.py": (
                "def median(xs):\n"
                '    """Return the median of xs."""\n'
                "    s = sorted(xs)\n"
                "    return s[len(s) // 2]\n"
            ),
            "tests/test_stats.py": (
                "from src.stats import median\n\n\n"
                "def test_odd_length():\n"
                "    assert median([3, 1, 2]) == 2\n\n\n"
                "def test_even_length_averages_middle_two():\n"
                "    assert median([1, 2, 3, 4]) == 2.5\n"
            ),
        },
    },
    {
        "id": "hc_02_bsearch",
        "objective": (
            "src/search.py has an off-by-one bug: binary_search() misses the last "
            "element of the list. Fix src/search.py so it finds every present element "
            "and returns -1 when absent. Do not modify any test."
        ),
        "files": {
            "src/__init__.py": "",
            "src/search.py": (
                "def binary_search(xs, target):\n"
                '    """Return the index of target in sorted xs, or -1."""\n'
                "    lo, hi = 0, len(xs) - 1\n"
                "    while lo < hi:\n"
                "        mid = (lo + hi) // 2\n"
                "        if xs[mid] == target:\n"
                "            return mid\n"
                "        if xs[mid] < target:\n"
                "            lo = mid + 1\n"
                "        else:\n"
                "            hi = mid - 1\n"
                "    return -1\n"
            ),
            "tests/test_search.py": (
                "from src.search import binary_search\n\n\n"
                "def test_finds_middle():\n"
                "    assert binary_search([1, 3, 5, 7, 9], 5) == 2\n\n\n"
                "def test_absent_returns_minus_one():\n"
                "    assert binary_search([1, 3, 5], 4) == -1\n\n\n"
                "def test_finds_last_element():\n"
                "    assert binary_search([1, 3, 5, 7, 9], 9) == 4\n"
            ),
        },
    },
    {
        "id": "hc_03_mutable_default",
        "objective": (
            "src/collect.py has a mutable-default-argument bug: add_item() shares one "
            "list across calls, so items leak between callers. Fix src/collect.py. "
            "Do not modify any test."
        ),
        "files": {
            "src/__init__.py": "",
            "src/collect.py": (
                "def add_item(item, bucket=[]):\n"
                '    """Append item to bucket and return it."""\n'
                "    bucket.append(item)\n"
                "    return bucket\n"
            ),
            "tests/test_collect.py": (
                "from src.collect import add_item\n\n\n"
                "def test_explicit_bucket_is_used():\n"
                "    b = [1]\n"
                "    assert add_item(2, b) == [1, 2]\n\n\n"
                "def test_calls_do_not_share_state():\n"
                "    assert add_item('a') == ['a']\n"
                "    assert add_item('b') == ['b']\n"
            ),
        },
    },
    {
        "id": "hc_04_retry_reraise",
        "objective": (
            "src/retry.py swallows the final exception: retry() returns None after "
            "exhausting its attempts instead of re-raising the last error. Fix "
            "src/retry.py so the last exception propagates. Do not modify any test."
        ),
        "files": {
            "src/__init__.py": "",
            "src/retry.py": (
                "def retry(fn, attempts=3):\n"
                '    """Call fn up to `attempts` times, returning its first success."""\n'
                "    for _ in range(attempts):\n"
                "        try:\n"
                "            return fn()\n"
                "        except ValueError:\n"
                "            pass\n"
                "    return None\n"
            ),
            "tests/test_retry.py": (
                "import pytest\n\n"
                "from src.retry import retry\n\n\n"
                "def test_returns_first_success():\n"
                "    calls = []\n\n"
                "    def flaky():\n"
                "        calls.append(1)\n"
                "        if len(calls) < 2:\n"
                "            raise ValueError('not yet')\n"
                "        return 'ok'\n\n"
                "    assert retry(flaky) == 'ok'\n\n\n"
                "def test_reraises_after_exhaustion():\n"
                "    def always_fails():\n"
                "        raise ValueError('boom')\n\n"
                "    with pytest.raises(ValueError):\n"
                "        retry(always_fails)\n"
            ),
        },
    },
    {
        "id": "hc_05_unicode_truncate",
        "objective": (
            "src/text.py has a bug: truncate() counts bytes instead of characters, so "
            "it mangles non-ASCII text, and it does not handle a limit shorter than the "
            "ellipsis. Fix src/text.py. Do not modify any test."
        ),
        "files": {
            "src/__init__.py": "",
            "src/text.py": (
                "def truncate(s, limit):\n"
                '    """Return s shortened to `limit` characters, with an ellipsis."""\n'
                "    raw = s.encode('utf-8')\n"
                "    if len(raw) <= limit:\n"
                "        return s\n"
                "    return raw[:limit - 3].decode('utf-8', 'ignore') + '...'\n"
            ),
            "tests/test_text.py": (
                "from src.text import truncate\n\n\n"
                "def test_short_string_unchanged():\n"
                "    assert truncate('hello', 10) == 'hello'\n\n\n"
                "def test_counts_characters_not_bytes():\n"
                "    # 6 CJK chars = 18 bytes; a 6-char limit must leave it untouched.\n"
                "    assert truncate('中文测试内容', 6) == '中文测试内容'\n\n\n"
                "def test_truncates_by_characters():\n"
                "    assert truncate('abcdefghij', 8) == 'abcde...'\n"
            ),
        },
    },
]

#: Injected into every fixture. Not decoration: a worker that runs the tests (which is
#: exactly what a good worker does here) generates `__pycache__/*.pyc`, `git add -A`
#: sweeps them into the diff as BINARY hunks, and `git apply` then refuses the whole
#: patch with "cannot apply binary patch ... without full index line". The first full
#: W3 run scored 1/5 for precisely this reason while having actually solved the tasks --
#: an apparatus defect that reads as incapability, which is the failure mode B0's
#: unparseable-reviewer-output problem already taught this project to look for.
#: Every real Python repo carries this file; the fixtures were simply unrealistic.
COMMON_FILES = {
    ".gitignore": "__pycache__/\n*.pyc\n.pytest_cache/\n",
}


def fixture_files(spec: dict) -> dict:
    """The bytes an arm actually receives: the task's files plus the common hygiene."""
    return {**COMMON_FILES, **spec["files"]}


#: What every arm is handed, identically. Budget is generous enough that no arm loses on
#: the clock for a task this size, so a failure is a failure to solve rather than to fit.
BUDGET = {"wall_min": 6, "quota_units": 1, "tokens_usd": 0.30,
          "tool_calls": 25, "inlane_retries": 0}


def as_contract(spec: dict) -> dict:
    """Render one spec into the contract shape every Chingis lane already accepts."""
    return {
        "contract_id": spec["id"],
        "lane": "",                       # filled by the arm
        "objective": spec["objective"],
        "context_refs": ["src/**"],       # tests/ is deliberately OUT of scope
        "capabilities": ["fs:worktree", "net:none", "proc:bash"],
        "output_schema": "diff+summary",
        "budget": dict(BUDGET),
        "verification": ["V1:pytest"],
    }
