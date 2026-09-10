"""Same-file collision index — Signal 1 of project coordination (Phase 1).

A *same-file collision* is two or more DISTINCT live sessions, tagged into the
same project, editing the same repo-relative file within a recency window. That
is the everyday merge-risk signal (a textual conflict is a same-file event
regardless of branch), so it is the primary collision signal.

This index is deliberately **in-memory, process-local runtime state** — NOT a
durable ``*.json`` store like ``ProjectStore``. The reasons are load-bearing:

* The data is ephemeral edit events evaluated against a 30-minute recency window
  and the set of *currently live* sessions. A gateway restart has no live
  sessions, so nothing here is meaningful to persist.
* The append happens on the hot turn path (per file-tool write). The design
  forbids disk/subprocess work there; an in-memory dict append is the only cost
  the hot path can bear. All git derivation (``repo_id``/``repo_rel_path``) is
  done OFF the loop, in the per-turn flush, before entries reach this index.

So this module owns only the pure, synchronous index math: record an edit,
prune by recency, and answer "which files in this project are contested by ≥2
distinct live sessions right now". Liveness and fork-lineage are supplied by the
caller (the dashboard owns the slot registry); this module never imports it, so
it stays a unit-testable leaf.

Model (from the peer-coordination design §4.2, Signal 1):

* Key is ``(project_group_id, repo_id, repo_rel_path)`` — repo-RELATIVE path, so
  two worktrees of one repo (different absolute paths) still match. Untagged
  sessions (``project_group_id == ""``) are never indexed.
* Collision = ``COUNT(DISTINCT session) >= 2`` for a key within the window,
  counting only sessions the caller reports as still live, and excluding a
  session paired with its own fork (a fork inherits the parent's recent edits).
* Recency window: 30 minutes for the notify path. Closed-session overlap is
  advisory-only (panel), never notified — so liveness is applied at query time,
  not at record time (a session that edited then closed still leaves its rows;
  the query filters them out for the live count).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

#: Recency window for the notify path, in seconds (design §4.2: 30 minutes).
RECENCY_WINDOW_SECS = 30 * 60


@dataclass(frozen=True)
class FileKey:
    """The identity of a contended file: project + repo + repo-relative path."""

    project_group_id: str
    repo_id: str
    repo_rel_path: str


@dataclass
class _Edit:
    """One session's newest write to a key: which session, when (epoch seconds).

    Mutable so ``record_edit`` can refresh ``ts`` in place when the same session
    re-edits (one row per session per key)."""

    session: str
    ts: float


class CollisionIndex:
    """In-memory same-file edit index. Thread-safe; pure index math only.

    The caller records an edit per file-tool write (off the hot loop), then
    queries for contested files supplying the current live-session set and a
    fork-pair predicate. This class knows nothing about slots, git, or disk.
    """

    def __init__(self, *, window_secs: float = RECENCY_WINDOW_SECS) -> None:
        self._window = window_secs
        # (project_group_id, repo_id, repo_rel_path) -> list[_Edit], newest last.
        self._by_key: dict[FileKey, list[_Edit]] = {}
        self._lock = threading.Lock()

    # ── record ────────────────────────────────────────────────────────────

    def record_edit(
        self,
        *,
        project_group_id: str,
        repo_id: str,
        repo_rel_path: str,
        session: str,
        ts: float | None = None,
    ) -> None:
        """Record that ``session`` edited a file. No-op for an untagged session
        or a missing key component — an untagged edit has no project to contest,
        and a missing repo_id/path means the caller could not resolve a
        well-defined key (an out-of-tree or sensitive write, dropped upstream).
        """
        if not (project_group_id and repo_id and repo_rel_path and session):
            return
        now = time.time() if ts is None else ts
        key = FileKey(project_group_id, repo_id, repo_rel_path)
        with self._lock:
            rows = self._by_key.setdefault(key, [])
            # Keep AT MOST ONE row per session (its newest edit): only the
            # DISTINCT-session set matters for a collision, so collapsing per
            # session both bounds the list by distinct-session-count (not
            # edit-count) and makes it impossible for one hammering session to
            # evict another session's row — the false-negative a newest-N-rows
            # cap caused (GPT-review data-loss BLOCK). A file contended by a
            # realistic handful of sessions holds a handful of rows no matter
            # how many times each edits.
            for e in rows:
                if e.session == session:
                    e.ts = now
                    break
            else:
                rows.append(_Edit(session=session, ts=now))
            self._prune_rows(rows, now)
            if not rows:
                self._by_key.pop(key, None)

    # ── query ─────────────────────────────────────────────────────────────

    def contested_files(
        self,
        project_group_id: str,
        *,
        live_sessions: set[str],
        is_fork_pair=None,
        now: float | None = None,
    ) -> list[tuple[FileKey, frozenset[str]]]:
        """Return the files in ``project_group_id`` contested by >= 2 distinct
        LIVE sessions within the recency window.

        ``live_sessions`` is the set of currently-live session identities (the
        caller's ``effective_session_key`` values). A row from a session not in
        this set is ignored for the live count — a closed session cannot be in a
        live race (design §4.2). ``is_fork_pair(a, b)`` (optional) returns True
        when two session identities are a parent/fork pair, which must NOT count
        as a collision (a fork inherits the parent's recent edits). ``now``
        overrides the clock for tests.

        Returns ``[(FileKey, frozenset_of_contending_sessions), ...]`` — only
        keys whose distinct live, non-fork-paired session set has >= 2 members.
        Pure read: does not mutate the index (pruning happens on record).
        """
        if not project_group_id:
            return []
        clock = time.time() if now is None else now
        cutoff = clock - self._window
        out: list[tuple[FileKey, frozenset[str]]] = []
        with self._lock:
            for key, rows in self._by_key.items():
                if key.project_group_id != project_group_id:
                    continue
                sessions = {
                    e.session
                    for e in rows
                    if e.ts >= cutoff and e.session in live_sessions
                }
                if len(sessions) < 2:
                    continue
                if is_fork_pair is not None and _all_fork_paired(sessions, is_fork_pair):
                    continue
                out.append((key, frozenset(sessions)))
        return out

    def distinct_session_count(
        self, key: FileKey, *, live_sessions: set[str], now: float | None = None
    ) -> int:
        """Distinct LIVE sessions contending ``key`` within the window. Used by
        the notify path's contested-ness suppression threshold (design §4.4)."""
        clock = time.time() if now is None else now
        cutoff = clock - self._window
        with self._lock:
            rows = self._by_key.get(key)
            if not rows:
                return 0
            return len({e.session for e in rows if e.ts >= cutoff and e.session in live_sessions})

    # ── maintenance ─────────────────────────────────────────────────────────

    def prune(self, *, now: float | None = None) -> None:
        """Drop all rows outside the recency window (and empty keys). Cheap to
        call periodically; record() already prunes the touched key."""
        clock = time.time() if now is None else now
        with self._lock:
            for key in list(self._by_key.keys()):
                rows = self._by_key[key]
                self._prune_rows(rows, clock)
                if not rows:
                    self._by_key.pop(key, None)

    def _prune_rows(self, rows: list[_Edit], now: float) -> None:
        # Scan ALL rows (not a prefix): rows are one-per-session and a session's
        # ts is refreshed in place, so they are NOT append-ordered by ts, and a
        # wall-clock that stepped backward could reorder them regardless. Rebuild
        # keeping only rows within the window.
        cutoff = now - self._window
        rows[:] = [e for e in rows if e.ts >= cutoff]


def _all_fork_paired(sessions: set[str], is_fork_pair) -> bool:
    """True when the ONLY reason ``sessions`` has >= 2 members is fork pairs —
    i.e. every distinct pair among them is a fork relationship, so there is no
    genuine cross-session contention. Conservative: with >2 sessions, if ANY
    pair is NOT a fork pair, it is a real collision (returns False).

    ``is_fork_pair`` MUST be symmetric: this checks each unordered pair once
    (``members[i], members[j]`` for i<j), so a directional predicate True only
    as ``(child, parent)`` would be missed in ``(parent, child)`` order and
    wrongly report a fork pair as a collision. The caller's predicate compares
    two session keys and returns True if EITHER is the other's ``forked_from`` —
    symmetric by construction.
    """
    members = sorted(sessions)
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            if not is_fork_pair(members[i], members[j]):
                return False
    return True
