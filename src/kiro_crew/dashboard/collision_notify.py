"""Collision notify decisions + notify-once dedupe (Phase 1, §4.4).

The two collision signals feed one notification path with strict noise
discipline, because a notification per same-file overlap would fire on the files
everyone touches (``__init__.py``, lockfiles, a barrel/router) and the user
would mute it within a day. So:

* **Same-worktree notifies by default** — rare and high-severity (a live
  filesystem race).
* **Same-file is panel-first**, and notifies ONLY under suppression: a runtime
  contested-ness threshold — a file touched by more than ``k`` distinct sessions
  is a high-churn shared file, self-evidently not a two-way merge risk, and is
  never notified. Plus a small static high-churn allowlist as an OPTIONAL
  pre-seed. Plus **notify-once per key** (never re-ping the same pair).
* The two signals are **deduped**: a same-worktree collision that is also
  same-file emits ONE notification.
* The notification body carries an **opaque project id, never the project
  name** — notifications persist to the agent-readable, unscoped
  ``notifications.jsonl``, so the name (and even "project X had a collision")
  would leak cross-project. The client resolves id->name at display time.

This module is pure decision + dedupe state; it does not call
``send_notification`` (the caller does, with the returned body). Thread-safe.
"""

from __future__ import annotations

import threading

#: A same-file key contested by MORE than this many distinct sessions is a
#: high-churn shared file, never notified (design §4.4 contested-ness threshold).
SAMEFILE_CONTESTED_MAX = 2

#: Optional static pre-seed of high-churn basenames that are never notified even
#: at the 2-session threshold. NOT the primary mechanism (the runtime threshold
#: is); just a cheap default so the obvious offenders never ping on day one.
_HIGH_CHURN_BASENAMES = frozenset(
    {"__init__.py", "changelog", "changelog.md", "uv.lock", "poetry.lock", "package-lock.json"}
)


def is_high_churn_basename(repo_rel_path: str) -> bool:
    base = repo_rel_path.rsplit("/", 1)[-1].lower()
    return base in _HIGH_CHURN_BASENAMES


class NotifyOnce:
    """Per-process seen-key store so a given collision pings at most once.

    Keyed on a stable signature of the collision (signal + project + the sorted
    session set + the contended thing), so the SAME pair on the SAME file/tree
    never re-notifies, but a NEW third session joining (a different session set)
    is a new signature and does notify once.

    Bounded lifecycle: each signature retains the session set it involved, and
    ``prune(live_sessions)`` drops any signature none of whose sessions are still
    live — a collision among sessions that have all closed cannot recur, so its
    entry is dead weight. Without this the set would grow unbounded over a
    long-lived process (GPT-review OOM class). The caller prunes each flush.
    """

    def __init__(self) -> None:
        # signature -> the session set that signature involved (for liveness prune)
        self._seen: dict[str, frozenset[str]] = {}
        self._lock = threading.Lock()

    def should_notify(self, signature: str, sessions: frozenset[str] = frozenset()) -> bool:
        """True exactly once per distinct signature; marks it seen atomically.
        ``sessions`` is retained so ``prune`` can drop the entry once they die."""
        if not signature:
            return False
        with self._lock:
            if signature in self._seen:
                return False
            self._seen[signature] = sessions
            return True

    def prune(self, live_sessions: set[str]) -> None:
        """Drop a signature once ANY of its participants is dead.

        A collision cannot recur unless its FULL original session set is still
        live, so a signature whose participants are not all live is dead weight.
        Keying on "all participants live" (not "any live") is what bounds the
        store: otherwise one long-lived session paired with a stream of
        short-lived peers would retain a signature per peer forever, because the
        long-lived one keeps every pair "partly alive" (GPT-review OOM class).
        """
        with self._lock:
            for sig in [
                s for s, sess in self._seen.items() if not sess.issubset(live_sessions)
            ]:
                self._seen.pop(sig, None)


def _sig(signal: str, project_group_id: str, contended: str, sessions: frozenset[str]) -> str:
    """Stable dedupe signature. Sorted sessions so order does not matter; the
    contended thing is a repo_rel_path (same-file) or a worktree_root
    (same-worktree). Never contains the project NAME — id only."""
    return "|".join([signal, project_group_id, contended, ",".join(sorted(sessions))])


def samefile_should_notify(
    *,
    project_group_id: str,
    repo_id: str,
    repo_rel_path: str,
    sessions: frozenset[str],
    notify_once: NotifyOnce,
) -> str | None:
    """Return a dedupe signature to notify on for a same-file collision, or None
    to stay panel-only. Suppressed when the file is high-churn (> threshold
    distinct sessions, or a known high-churn basename) or already notified.

    ``repo_id`` is part of the dedupe key: the SAME repo_rel_path in two
    DIFFERENT repos is a distinct collision, so folding repo_id in stops the
    second one being suppressed as a duplicate (GPT-review dedupe finding)."""
    if len(sessions) > SAMEFILE_CONTESTED_MAX:
        return None  # high-churn shared file -> panel-only, never notify
    if is_high_churn_basename(repo_rel_path):
        return None
    sig = _sig("same-file", project_group_id, f"{repo_id}/{repo_rel_path}", sessions)
    return sig if notify_once.should_notify(sig, sessions) else None


def sameworktree_should_notify(
    *,
    project_group_id: str,
    worktree_root: str,
    sessions: frozenset[str],
    notify_once: NotifyOnce,
) -> str | None:
    """Return a dedupe signature to notify on for a same-worktree collision, or
    None if already notified. Same-worktree notifies by default (no churn
    suppression) — it is the rare, high-severity signal."""
    sig = _sig("same-worktree", project_group_id, worktree_root, sessions)
    return sig if notify_once.should_notify(sig, sessions) else None


def notification_body(*, project_group_id: str, signal: str, session_count: int) -> str:
    """The notification body — OPAQUE project id, never the name, and no file
    path or worktree path (those persist to the unscoped notifications.jsonl).
    Plain text. The client resolves id->name and links to the panel for detail.
    """
    what = (
        "sessions are working the same worktree (live filesystem race)"
        if signal == "same-worktree"
        else "sessions edited the same file (possible merge conflict)"
    )
    n = max(session_count, 2)  # a collision is >= 2 by definition
    return (
        f"Project coordination: {n} {what}. "
        f"In project {project_group_id}. Open the project panel for details."
    )
