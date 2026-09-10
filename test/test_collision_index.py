"""Tests for the same-file collision index (Signal 1 index math)."""

from __future__ import annotations

from kiro_crew.dashboard.collision_index import (
    RECENCY_WINDOW_SECS,
    CollisionIndex,
    FileKey,
)

_P = "grp-1"
_R = "git@host:org/repo.git"
_F = "src/app.py"


def _rec(idx, session, ts, *, project=_P, repo=_R, path=_F):
    idx.record_edit(
        project_group_id=project, repo_id=repo, repo_rel_path=path, session=session, ts=ts
    )


class TestRecordAndContest:
    def test_two_distinct_live_sessions_collide(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        _rec(idx, "sess-b", 1001.0)
        hits = idx.contested_files(_P, live_sessions={"sess-a", "sess-b"}, now=1002.0)
        assert len(hits) == 1
        key, sessions = hits[0]
        assert key == FileKey(_P, _R, _F)
        assert sessions == frozenset({"sess-a", "sess-b"})

    def test_single_session_re_editing_does_not_self_collide(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        _rec(idx, "sess-a", 1001.0)
        _rec(idx, "sess-a", 1002.0)
        assert idx.contested_files(_P, live_sessions={"sess-a"}, now=1003.0) == []

    def test_closed_session_not_counted_for_live_collision(self):
        # sess-b edited but is no longer live -> only sess-a is live -> no collision.
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        _rec(idx, "sess-b", 1001.0)
        assert idx.contested_files(_P, live_sessions={"sess-a"}, now=1002.0) == []

    def test_edit_outside_window_is_ignored(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        # sess-b edited long ago, outside the 30-min window.
        _rec(idx, "sess-b", 1000.0 - RECENCY_WINDOW_SECS - 10)
        assert idx.contested_files(
            _P, live_sessions={"sess-a", "sess-b"}, now=1001.0
        ) == []

    def test_untagged_edit_is_not_indexed(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0, project="")
        _rec(idx, "sess-b", 1001.0, project="")
        assert idx.contested_files("", live_sessions={"sess-a", "sess-b"}, now=1002.0) == []

    def test_missing_key_component_is_dropped(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0, repo="")  # no repo_id
        _rec(idx, "sess-b", 1001.0, path="")  # no path
        assert idx.contested_files(_P, live_sessions={"sess-a", "sess-b"}, now=1002.0) == []

    def test_different_repo_rel_paths_do_not_collide(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0, path="src/a.py")
        _rec(idx, "sess-b", 1001.0, path="src/b.py")
        assert idx.contested_files(_P, live_sessions={"sess-a", "sess-b"}, now=1002.0) == []

    def test_same_relpath_across_worktrees_collides(self):
        # Two worktrees of one repo: same repo_id + repo_rel_path, different
        # sessions. The whole point of keying on repo-relative path.
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        _rec(idx, "sess-b", 1001.0)
        hits = idx.contested_files(_P, live_sessions={"sess-a", "sess-b"}, now=1002.0)
        assert len(hits) == 1

    def test_other_projects_not_returned(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0, project="grp-1")
        _rec(idx, "sess-b", 1001.0, project="grp-2")
        assert idx.contested_files("grp-1", live_sessions={"sess-a", "sess-b"}, now=1002.0) == []


class TestForkExclusion:
    def test_parent_and_its_fork_do_not_collide(self):
        idx = CollisionIndex()
        _rec(idx, "parent", 1000.0)
        _rec(idx, "child", 1001.0)
        # child is a fork of parent.
        pair = lambda a, b: {a, b} == {"parent", "child"}  # noqa: E731
        assert idx.contested_files(
            _P, live_sessions={"parent", "child"}, is_fork_pair=pair, now=1002.0
        ) == []

    def test_third_unrelated_session_still_collides_with_fork_pair(self):
        idx = CollisionIndex()
        _rec(idx, "parent", 1000.0)
        _rec(idx, "child", 1001.0)
        _rec(idx, "stranger", 1002.0)
        pair = lambda a, b: {a, b} == {"parent", "child"}  # noqa: E731
        hits = idx.contested_files(
            _P, live_sessions={"parent", "child", "stranger"}, is_fork_pair=pair, now=1003.0
        )
        # stranger vs parent (and vs child) are real pairs -> collision stands.
        assert len(hits) == 1
        assert hits[0][1] == frozenset({"parent", "child", "stranger"})

    def test_all_fork_paired_triple_is_suppressed(self):
        # Three sessions where EVERY pair is a fork relationship (a chain
        # a<-b<-c all sharing lineage) -> no genuine contention -> suppressed.
        idx = CollisionIndex()
        _rec(idx, "a", 1000.0)
        _rec(idx, "b", 1001.0)
        _rec(idx, "c", 1002.0)
        all_pairs = lambda x, y: True  # noqa: E731  every pair is a fork pair
        assert idx.contested_files(
            _P, live_sessions={"a", "b", "c"}, is_fork_pair=all_pairs, now=1003.0
        ) == []

    def test_fork_pair_predicate_checked_symmetrically(self):
        # The index checks each unordered pair once in sorted order; a symmetric
        # predicate must suppress regardless of which argument is "parent".
        idx = CollisionIndex()
        _rec(idx, "zeta", 1000.0)  # sorts AFTER "alpha"
        _rec(idx, "alpha", 1001.0)
        # Symmetric predicate keyed on the set, so order does not matter.
        pair = lambda a, b: {a, b} == {"alpha", "zeta"}  # noqa: E731
        assert idx.contested_files(
            _P, live_sessions={"alpha", "zeta"}, is_fork_pair=pair, now=1002.0
        ) == []


class TestRowCap:
    def test_hammering_session_never_evicts_another_distinct_session(self):
        # The false-negative GPT caught: session B edits once, session A edits
        # the same file many times. With one-row-per-session, B's row is never
        # evicted, so the collision still fires (kills the newest-N-rows cap).
        idx = CollisionIndex()
        _rec(idx, "sess-b", 1000.0)
        for i in range(500):  # A hammers far past any old row cap
            _rec(idx, "sess-a", 1001.0 + i)
        hits = idx.contested_files(
            _P, live_sessions={"sess-a", "sess-b"}, now=1002.0 + 500
        )
        assert len(hits) == 1
        assert hits[0][1] == frozenset({"sess-a", "sess-b"})

    def test_one_row_per_session_regardless_of_edit_count(self):
        # Structure is bounded by distinct sessions, not edits: a session
        # editing N times leaves exactly one row (its newest ts).
        idx = CollisionIndex()
        for i in range(100):
            _rec(idx, "solo", 1000.0 + i)
        key = FileKey(_P, _R, _F)
        # Only 'solo' present -> not a collision, and its single row is recent.
        assert idx.distinct_session_count(key, live_sessions={"solo"}, now=1100.0) == 1


class TestSuppressionCount:
    def test_distinct_session_count_for_threshold(self):
        idx = CollisionIndex()
        for i, s in enumerate(["a", "b", "c", "d"]):
            _rec(idx, s, 1000.0 + i)
        key = FileKey(_P, _R, _F)
        live = {"a", "b", "c", "d"}
        assert idx.distinct_session_count(key, live_sessions=live, now=1005.0) == 4
        # A high-churn file (> k sessions) is what the notify path suppresses.
        assert idx.distinct_session_count(key, live_sessions={"a"}, now=1005.0) == 1


class TestPrune:
    def test_prune_drops_stale_rows_and_empty_keys(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        idx.prune(now=1000.0 + RECENCY_WINDOW_SECS + 10)
        # All rows stale -> key removed -> no collision possible.
        assert idx.contested_files(
            _P, live_sessions={"sess-a"}, now=1000.0 + RECENCY_WINDOW_SECS + 11
        ) == []

    def test_record_prunes_touched_key(self):
        idx = CollisionIndex()
        _rec(idx, "sess-a", 1000.0)
        # A much later edit by b prunes a's stale row on the same key.
        _rec(idx, "sess-b", 1000.0 + RECENCY_WINDOW_SECS + 10)
        # a's row is now stale/pruned; only b remains -> no 2-session collision.
        assert idx.contested_files(
            _P,
            live_sessions={"sess-a", "sess-b"},
            now=1000.0 + RECENCY_WINDOW_SECS + 11,
        ) == []
