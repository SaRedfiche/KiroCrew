"""Remote-target collision — Signal 3 of project coordination (Phase 1).

A *remote-target collision* is two or more live sessions whose current branch
pushes to the SAME upstream ref (same remote, same remote branch). This is the
real "two sessions, one PR" incident: two sessions in a repo, possibly in
SEPARATE worktrees editing DIFFERENT files — invisible to Signal 1 (same-file)
and Signal 2 (same-worktree) — each with a local branch tracking
``origin/feature/x``. When both push, the second clobbers or is rejected, and
the PR history is a race. The hazard is the shared PUSH TARGET, not the tree or
the file.

Unlike Signal 1 this is NOT recency-windowed: it is about sessions that share a
push target RIGHT NOW. So the state is a simple ``session -> remote_target`` map
of each live session's current upstream ref; a collision is any target held by
>= 2 distinct live sessions. Liveness is supplied by the caller at query time
(the dashboard owns the slot registry), so a closed session's stale entry is
ignored and swept.

Design (peer-coordination design §4.2 Signal 3, §4.4):

* The ``remote_target`` is ``canonicalize_remote(push_url) + "#" + remote_ref``
  — the canonical remote identity plus the upstream branch — derived off-loop
  from ``git rev-parse --abbrev-ref --symbolic-full-name @{u}`` and the resolved
  push URL. Canonicalizing the URL means a session on the scp-form remote and
  one on the https-form of the same repo still collide. A branch with NO
  upstream configured contributes nothing (no shared target to race).
* Collision = two distinct live sessions sharing one ``remote_target``. NOT
  same-branch-locally and NOT same-repo: only a shared PUSH TARGET fires. Two
  sessions on differently-named local branches that both track
  ``origin/main`` collide; two on the same local branch name tracking different
  upstreams do not.
* Fork pairs are excluded like Signals 1 and 2 (a fork that has not yet
  retargeted momentarily shares the parent's upstream; that is not the hazard).
* This module is a pure, thread-safe leaf: it knows nothing about slots, git, or
  ``send_notification``. The notify decision (default-notify for a shared push
  target, dedupe, scope-free body) lives in the caller.
"""

from __future__ import annotations

import threading

from kiro_crew.dashboard.collision_index import _all_fork_paired


class RemoteTargetIndex:
    """Live ``session -> remote_target`` map. Thread-safe; pure set math.

    The caller updates a session's current push target on each per-turn flush
    (derived off-loop from the session's cwd), lets a closed session's row be
    swept by ``prune``, and queries for targets shared by >= 2 distinct live
    sessions. Because the update is per-flush, co-targeting is eventually
    consistent — a session appears only after its first turn.
    """

    def __init__(self) -> None:
        self._by_session: dict[str, str] = {}
        self._lock = threading.Lock()

    def set_target(self, session: str, remote_target: str) -> None:
        """Record that ``session``'s branch now pushes to ``remote_target``. An
        empty target (no upstream configured / derive miss) drops the session —
        a branch with no push target cannot be in a push race."""
        if not session:
            return
        with self._lock:
            if remote_target:
                self._by_session[session] = remote_target
            else:
                self._by_session.pop(session, None)

    def cotargets(
        self, remote_target: str, *, live_sessions: set[str], is_fork_pair=None
    ) -> frozenset[str]:
        """The distinct LIVE sessions currently pushing to ``remote_target``.

        Returns the co-targeting session set (>= 2 members means a collision). A
        session not in ``live_sessions`` is ignored (closed sessions do not
        race). If ``is_fork_pair`` is given and the ONLY co-targeters are a
        single fork pair, returns an empty set (a fork momentarily on the
        parent's upstream is not the hazard) — same conservative rule as Signals
        1 and 2.
        """
        if not remote_target:
            return frozenset()
        with self._lock:
            members = {
                s
                for s, target in self._by_session.items()
                if target == remote_target and s in live_sessions
            }
        if len(members) < 2:
            return frozenset()
        if is_fork_pair is not None and _all_fork_paired(members, is_fork_pair):
            return frozenset()
        return frozenset(members)

    def prune(self, *, live_sessions: set[str]) -> None:
        """Drop entries for sessions that are not live. Cheap; call periodically
        so a long-lived process does not accumulate closed-session rows."""
        with self._lock:
            for s in [s for s in self._by_session if s not in live_sessions]:
                self._by_session.pop(s, None)
