"""Tests for the remote-target index (Signal 3) and its notify decision."""

from __future__ import annotations

from kiro_crew.dashboard.collision_notify import (
    NotifyOnce,
    notification_body,
    sameremote_should_notify,
)
from kiro_crew.dashboard.remote_target_index import RemoteTargetIndex

_TARGET = "github.com/org/repo#feature/x"


class TestRemoteTargetIndex:
    def test_two_sessions_sharing_target_collide(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", _TARGET)
        idx.set_target("sess-b", _TARGET)
        assert idx.cotargets(_TARGET, live_sessions={"sess-a", "sess-b"}) == frozenset(
            {"sess-a", "sess-b"}
        )

    def test_single_session_is_not_a_collision(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", _TARGET)
        assert idx.cotargets(_TARGET, live_sessions={"sess-a"}) == frozenset()

    def test_different_targets_do_not_collide(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", "github.com/org/repo#feature/a")
        idx.set_target("sess-b", "github.com/org/repo#feature/b")
        # Same repo, different upstream branches -> no shared push target.
        assert (
            idx.cotargets("github.com/org/repo#feature/a", live_sessions={"sess-a", "sess-b"})
            == frozenset()
        )
        assert (
            idx.cotargets("github.com/org/repo#feature/b", live_sessions={"sess-a", "sess-b"})
            == frozenset()
        )

    def test_differently_named_local_branches_same_upstream_collide(self):
        # The target is the UPSTREAM ref, not the local branch name: two
        # sessions on locally-distinct branches that both track origin/main
        # share one push target and must collide.
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", "github.com/org/repo#main")
        idx.set_target("sess-b", "github.com/org/repo#main")
        assert idx.cotargets(
            "github.com/org/repo#main", live_sessions={"sess-a", "sess-b"}
        ) == frozenset({"sess-a", "sess-b"})

    def test_closed_session_not_counted(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", _TARGET)
        idx.set_target("sess-b", _TARGET)
        assert idx.cotargets(_TARGET, live_sessions={"sess-a"}) == frozenset()

    def test_empty_target_drops_session(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", _TARGET)
        idx.set_target("sess-a", "")  # upstream unset now (detached / never pushed)
        assert idx.cotargets(_TARGET, live_sessions={"sess-a"}) == frozenset()

    def test_fork_pair_excluded_but_third_stranger_collides(self):
        idx = RemoteTargetIndex()
        idx.set_target("parent", _TARGET)
        idx.set_target("child", _TARGET)
        pair = lambda a, b: {a, b} == {"parent", "child"}  # noqa: E731
        assert (
            idx.cotargets(_TARGET, live_sessions={"parent", "child"}, is_fork_pair=pair)
            == frozenset()
        )
        idx.set_target("stranger", _TARGET)
        assert idx.cotargets(
            _TARGET, live_sessions={"parent", "child", "stranger"}, is_fork_pair=pair
        ) == frozenset({"parent", "child", "stranger"})

    def test_prune_drops_dead_sessions(self):
        idx = RemoteTargetIndex()
        idx.set_target("sess-a", _TARGET)
        idx.set_target("sess-b", _TARGET)
        idx.prune(live_sessions={"sess-a"})  # b died
        assert idx.cotargets(_TARGET, live_sessions={"sess-a", "sess-b"}) == frozenset()


class TestSameRemoteNotifyDecision:
    def test_notifies_by_default_and_dedupes(self):
        n = NotifyOnce()
        sessions = frozenset({"a", "b"})
        assert sameremote_should_notify(remote_target=_TARGET, sessions=sessions, notify_once=n)
        assert (
            sameremote_should_notify(
                remote_target=_TARGET,
                sessions=sessions,
                notify_once=n,
            )
            is None
        )

    def test_no_churn_suppression_for_remote_target(self):
        # Like same-worktree, more sessions racing one PR is MORE severe, not
        # less — no churn threshold.
        n = NotifyOnce()
        assert sameremote_should_notify(
            remote_target=_TARGET,
            sessions=frozenset({"a", "b", "c"}),
            notify_once=n,
        )

    def test_distinct_from_same_worktree_signature(self):
        # The same string value but different SIGNAL must be a distinct dedupe
        # signature, so a same-worktree note does not suppress a same-remote
        # note (and vice versa).
        from kiro_crew.dashboard.collision_notify import sameworktree_should_notify

        n = NotifyOnce()
        s = frozenset({"a", "b"})
        assert sameworktree_should_notify(worktree_root=_TARGET, sessions=s, notify_once=n)
        # Same string, DIFFERENT signal -> still notifies.
        assert sameremote_should_notify(remote_target=_TARGET, sessions=s, notify_once=n)


class TestSameRemoteBody:
    def test_body_describes_the_push_race_without_leaking_the_target(self):
        body = notification_body(signal="same-remote", session_count=2)
        assert "remote branch" in body or "PR" in body
        # The remote identity (host/org/repo#ref) must never reach the body.
        assert "github.com" not in body
        assert "#" not in body

    def test_body_count_matches_session_count(self):
        body = notification_body(signal="same-remote", session_count=3)
        assert "3 sessions" in body
