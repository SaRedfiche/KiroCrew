#!/usr/bin/env python3
"""AcceptSpec evaluator - the deterministic half of the conductor's patrol.

The conductor NEVER judges whether a work item succeeded; this script does,
and the conductor only reads its verdicts. Script-first: the decision is an
exit code and a JSON document, not a model's impression of a transcript.

Usage:
    python3 accept_eval.py < items.json

stdin (JSON):
    {"items": [
        {"id": "item-1", "accept": {"kind": "cmd", "argv": ["pytest", "tests/x.py"],
                                     "cwd": "/abs/path"}},
        {"id": "item-2", "accept": {"kind": "pr_checks", "pr": 123,
                                     "repo": "owner/name"}},
        {"id": "item-3", "accept": {"kind": "file", "path": "/abs/path",
                                     "exists": true}}
    ]}

stdout (JSON):
    {"results": [{"id": "...", "verdict": "pass|fail|pending|refused|error",
                  "evidence": "..."}]}

Exit code: 0 when evaluation ran (verdicts carry the outcome); 2 on malformed
input. A per-item problem is a verdict, never a crash - one bad spec must not
hide the others' results.

Security posture (deliberate, do not weaken):
- cmd argv[0] basename must sit in ALLOWED_COMMANDS. A spec is model-authored
  and this script runs unattended, so the allowlist - not the model's
  judgment - is what bounds what can execute. A refused spec is a "refused"
  verdict the conductor must surface to the user, never retry around.
- No shell. argv arrays only, subprocess.run(shell=False).
- Per-check timeout (TIMEOUT_SECS). A hung check is a "error" verdict.
- Evidence is tail-capped so a chatty test cannot flood the conductor's turn.

Stdlib-only, Python 3.8+.
"""

import json
import subprocess
import sys
from pathlib import Path

ALLOWED_COMMANDS = {
    "pytest",
    "python",
    "python3",
    "gh",
    "git",
    "npm",
    "npx",
    "node",
    "make",
    "cargo",
    "go",
}

TIMEOUT_SECS = 300
EVIDENCE_TAIL_CHARS = 500

#: gh pr checks exits 8 when checks are still running (gh >= 2.30).
_GH_PENDING_EXIT = 8


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text[-EVIDENCE_TAIL_CHARS:] if len(text) > EVIDENCE_TAIL_CHARS else text


def _run(argv, cwd=None):
    """Run one check without a shell; return (verdict, evidence)."""
    base = Path(str(argv[0])).name
    if base not in sorted(ALLOWED_COMMANDS):
        return (
            "refused",
            f"command {base!r} is not in the evaluator allowlist "
            f"({', '.join(sorted(ALLOWED_COMMANDS))}); surface this to the user",
        )
    try:
        proc = subprocess.run(  # noqa: S603 - argv array, no shell, allowlisted
            [str(a) for a in argv],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return ("error", f"timed out after {TIMEOUT_SECS}s")
    except FileNotFoundError:
        return ("error", f"{argv[0]!r} not found on PATH")
    except OSError as exc:
        return ("error", f"could not run: {exc}")
    output = _tail(proc.stdout + "\n" + proc.stderr)
    if proc.returncode == 0:
        return ("pass", output or "exit 0")
    if base == "gh" and proc.returncode == _GH_PENDING_EXIT:
        return ("pending", output or "checks still running")
    return ("fail", f"exit {proc.returncode}: {output}")


def _evaluate(item):
    accept = item.get("accept") or {}
    kind = accept.get("kind")
    if kind == "cmd":
        argv = accept.get("argv")
        if not isinstance(argv, list) or not argv:
            return ("error", "cmd spec needs a non-empty argv array")
        return _run(argv, cwd=accept.get("cwd"))
    if kind == "pr_checks":
        pr = accept.get("pr")
        if not isinstance(pr, int):
            return ("error", "pr_checks spec needs an integer pr")
        argv = ["gh", "pr", "checks", str(pr)]
        repo = accept.get("repo")
        if repo:
            argv += ["--repo", str(repo)]
        return _run(argv)
    if kind == "file":
        path = accept.get("path")
        if not isinstance(path, str) or not path:
            return ("error", "file spec needs a path")
        want = bool(accept.get("exists", True))
        have = Path(path).exists()
        verdict = "pass" if have == want else "fail"
        return (verdict, f"{path} {'exists' if have else 'does not exist'}")
    if kind == "human_approval":
        # Never machine-evaluated; the conductor asks the person.
        return ("pending", "awaiting human approval - not machine-checkable")
    return ("error", f"unknown accept kind {kind!r}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        items = payload["items"]
        assert isinstance(items, list)
    except Exception:
        print(json.dumps({"error": "stdin must be JSON: {\"items\": [...]}"}))
        return 2
    results = []
    for item in items:
        item_id = str(item.get("id", "?"))
        try:
            verdict, evidence = _evaluate(item)
        except Exception as exc:  # one bad spec must not hide the rest
            verdict, evidence = "error", f"evaluator bug on this item: {exc}"
        results.append({"id": item_id, "verdict": verdict, "evidence": evidence})
    print(json.dumps({"results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
