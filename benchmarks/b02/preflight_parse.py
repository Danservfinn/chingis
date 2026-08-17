#!/usr/bin/env python3
"""B0.2 §4.1 gate: can each reviewer identity actually honour the rubric's output contract?

    ./benchmarks/b02/preflight_parse.py

B0's single largest apparatus defect was that **5 of 24 reviewer outputs were
unparseable and scored as zero findings** — a silent, one-directional bias on the
catch rate. Its own report said so: *"if this is more than a couple, the rubric's output
contract is failing and the catch rates are biased downward."*

B0.2 adds reviewer identities the rubric has never been exercised against, so that risk
goes up, not down. This gate therefore runs FIRST and on **throwaway artifacts**: a
reviewer below the §5 floor (≥90%) is excluded *before* any scored data exists, which is
the only point at which excluding a reviewer is not a result-shaping decision.

It deliberately measures ONLY parseability — never whether the reviewer was right. Reading
correctness here would be reading the outcome before the experiment starts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RUBRIC = ROOT / "verify" / "rubric" / "review_rubric.md"
SENTINEL = "<!-- ===== PROMPT BEGINS ===== -->"
OUT = Path(__file__).resolve().parent / "results"

FLOOR = 0.90                    # §5, derived: above the 79% B0 itself called broken
VALID_VERDICTS = {"approve", "reject"}

ZAI_CODING = os.environ.get("ZAI_CODING_ENDPOINT") or "https://api.z.ai/api/coding/paas/v4"
ZAI_ANTHROPIC = os.environ.get("ZAI_ANTHROPIC_ENDPOINT") or "https://api.z.ai/api/anthropic"

#: Throwaway artifacts — deliberately NOT from the scored corpus. Each is a small diff
#: with an obvious defect, so a reviewer has something real to report and the only thing
#: under test is whether it can emit the required shape.
THROWAWAY = [
    ("off_by_one", """--- a/src/window.py\n+++ b/src/window.py\n@@\n def last_n(xs, n):\n-    return xs[-n:]\n+    return xs[-n:-1]\n"""),
    ("swallowed_exception", """--- a/src/io.py\n+++ b/src/io.py\n@@\n def load(p):\n     try:\n         return open(p).read()\n-    except OSError:\n-        raise\n+    except OSError:\n+        pass\n"""),
    ("hardcoded_secret", """--- a/src/client.py\n+++ b/src/client.py\n@@\n def key():\n-    return os.environ.get("API_KEY")\n+    return "sk-live-9f2b41c7d8e6"\n"""),
    ("encoding_bug", """--- a/src/text.py\n+++ b/src/text.py\n@@\n def head(s, n):\n-    return s[:n]\n+    return s.encode()[:n].decode("utf-8", "ignore")\n"""),
    ("clean", """--- a/src/math_utils.py\n+++ b/src/math_utils.py\n@@\n def mean(xs):\n-    return sum(xs) / len(xs)\n+    if not xs:\n+        raise ValueError("mean of empty sequence")\n+    return sum(xs) / len(xs)\n"""),
]


def rubric_prompt(diff: str) -> str:
    text = RUBRIC.read_text()
    body = text.split(SENTINEL, 1)[1] if SENTINEL in text else text
    return (f"{body}\n\n## Change under review\n\n```diff\n{diff}\n```\n"
            "\nEmit exactly one fenced JSON block and nothing after it.")


# ------------------------------------------------------------------ parsing --
def parse_review(text: str) -> tuple[bool, str]:
    """Exactly B0's contract: one fenced JSON block with `findings` and `verdict`."""
    if not text or not text.strip():
        return False, "empty response"
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    blob = m.group(1) if m else None
    if blob is None:
        m2 = re.search(r"\{.*\}", text, re.S)          # be generous: unfenced but valid
        blob = m2.group(0) if m2 else None
    if blob is None:
        return False, "no JSON object found"
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {str(e)[:60]}"
    if not isinstance(obj.get("findings"), list):
        return False, "`findings` missing or not a list"
    if obj.get("verdict") not in VALID_VERDICTS:
        return False, f"verdict={obj.get('verdict')!r} not in {sorted(VALID_VERDICTS)}"
    return True, f"ok ({len(obj['findings'])} findings, {obj['verdict']})"


# ---------------------------------------------------------------- reviewers --
def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=300))


def review_glm53(prompt: str) -> str:
    r = _post(ZAI_CODING + "/chat/completions",
              {"model": "glm-5.3", "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 24000},
              {"Authorization": "Bearer " + os.environ["ZAI_API_KEY"],
               "Content-Type": "application/json"})
    return r["choices"][0]["message"].get("content") or ""


def review_glm47(prompt: str) -> str:
    key = os.environ.get("ZAI_KEY") or os.environ["ZAI_API_KEY"]
    r = _post(ZAI_ANTHROPIC + "/v1/messages",
              {"model": "claude-sonnet-4-5", "max_tokens": 4000,
               "messages": [{"role": "user", "content": prompt}]},
              {"x-api-key": key, "anthropic-version": "2023-06-01",
               "Content-Type": "application/json"})
    return "".join(b.get("text", "") for b in r.get("content", []))


def review_claude(prompt: str) -> str:
    """W1's path: Claude Code headless, native Anthropic, subscription OAuth."""
    from adapters.claude_adapter import _clean_env
    p = subprocess.run([os.environ.get("CLAUDE_BIN", "claude"), "-p",
                        "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, timeout=300,
                       env=_clean_env())
    try:
        return json.loads(p.stdout).get("result", p.stdout)
    except (json.JSONDecodeError, AttributeError):
        return p.stdout or ""


REVIEWERS = {"glm-5.3": review_glm53, "glm-4.7": review_glm47,
             "claude-sonnet": review_claude}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:] or list(REVIEWERS)
    rows, summary = [], {}

    for name in only:
        fn = REVIEWERS[name]
        ok_n = 0
        print(f"\n== {name} ==", flush=True)
        for cls, diff in THROWAWAY:
            try:
                text = fn(rubric_prompt(diff))
            except Exception as e:                             # noqa: BLE001
                print(f"  {cls:<22} TRANSPORT ERROR {type(e).__name__}", flush=True)
                rows.append({"reviewer": name, "artifact": cls, "parsed": False,
                             "detail": f"transport: {type(e).__name__}"})
                continue
            ok, detail = parse_review(text)
            ok_n += ok
            print(f"  {cls:<22} {'PARSE' if ok else 'FAIL '}  {detail[:70]}", flush=True)
            rows.append({"reviewer": name, "artifact": cls, "parsed": ok,
                         "detail": detail})
        rate = ok_n / len(THROWAWAY)
        summary[name] = rate
        print(f"  -> parse rate {ok_n}/{len(THROWAWAY)} = {rate:.0%}  "
              f"{'ADMITTED' if rate >= FLOOR else 'EXCLUDED (below §5 floor)'}", flush=True)

    (OUT / "preflight_parse.json").write_text(
        json.dumps({"floor": FLOOR, "summary": summary, "rows": rows}, indent=2))

    print(f"\n=== §4.1 gate (floor {FLOOR:.0%}) ===")
    admitted = [n for n, r in summary.items() if r >= FLOOR]
    for n, r in summary.items():
        print(f"  {n:<16} {r:.0%}  {'admitted' if r >= FLOOR else 'EXCLUDED'}")
    print(f"\n{len(admitted)} of {len(summary)} reviewer identities admitted.")
    if len(admitted) < 3:
        print("B0.2 needs 3 identities to separate identity from lineage. Below that the")
        print("design cannot answer its own question and must not be run as though it can.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
