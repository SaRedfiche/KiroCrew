"""Tests for the same-worktree index (Signal 2) and collision notify decisions."""

from __future__ import annotations

from kiro_crew.dashboard.collision_notify import (
    NotifyOnce,
    notification_body,
    samefile_should_notify,
    sameworktree_should_notify,
)
from kiro_crew.dashboard.worktree_index import WorktreeIndex

_WT = "/repo/worktree-a"


class TestWorktreeIndex:
    def test_two_sessions_sharing_tree_collide(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", _WT)
        idx.set_worktree("sess-b", _WT)
        assert idx.cotenants(_WT, live_sessions={"sess-a", "sess-b"}) == frozenset(
            {"sess-a", "sess-b"}
        )

    def test_single_session_is_not_a_collision(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", _WT)
        assert idx.cotenants(_WT, live_sessions={"sess-a"}) == frozenset()

    def test_different_trees_do_not_collide(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", "/repo/wt-a")
        idx.set_worktree("sess-b", "/repo/wt-b")
        assert idx.all_collisions(live_sessions={"sess-a", "sess-b"}) == []

    def test_closed_session_not_counted(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", _WT)
        idx.set_worktree("sess-b", _WT)
        # sess-b closed -> only a live -> no collision.
        assert idx.cotenants(_WT, live_sessions={"sess-a"}) == frozenset()

    def test_empty_root_drops_session(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", _WT)
        idx.set_worktree("sess-a", "")  # non-repo cwd now
        assert idx.cotenants(_WT, live_sessions={"sess-a"}) == frozenset()

    def test_fork_pair_excluded_but_third_stranger_collides(self):
        idx = WorktreeIndex()
        idx.set_worktree("parent", _WT)
        idx.set_worktree("child", _WT)
        pair = lambda a, b: {a, b} == {"parent", "child"}  # noqa: E731
        assert idx.cotenants(
            _WT, live_sessions={"parent", "child"}, is_fork_pair=pair
        ) == frozenset()
        idx.set_worktree("stranger", _WT)
        assert idx.cotenants(
            _WT, live_sessions={"parent", "child", "stranger"}, is_fork_pair=pair
        ) == frozenset({"parent", "child", "stranger"})

    def test_prune_drops_dead_sessions(self):
        idx = WorktreeIndex()
        idx.set_worktree("sess-a", _WT)
        idx.set_worktree("sess-b", _WT)
        idx.prune(live_sessions={"sess-a"})  # b died
        assert idx.cotenants(_WT, live_sessions={"sess-a", "sess-b"}) == frozenset()

    def test_all_collisions_lists_shared_trees_only(self):
        idx = WorktreeIndex()
        idx.set_worktree("a", "/wt/shared")
        idx.set_worktree("b", "/wt/shared")
        idx.set_worktree("c", "/wt/solo")
        hits = idx.all_collisions(live_sessions={"a", "b", "c"})
        assert len(hits) == 1
        assert hits[0][0] == "/wt/shared"
        assert hits[0][1] == frozenset({"a", "b"})


class TestNotifyOnce:
    def test_notifies_once_per_signature(self):
        n = NotifyOnce()
        assert n.should_notify("sig-1", frozenset({"a", "b"})) is True
        assert n.should_notify("sig-1", frozenset({"a", "b"})) is False  # already sent
        assert n.should_notify("sig-2", frozenset({"a", "c"})) is True  # distinct

    def test_prune_drops_signature_when_any_participant_dead(self):
        n = NotifyOnce()
        n.should_notify("sig-ab", frozenset({"a", "b"}))
        n.should_notify("sig-ac", frozenset({"a", "c"}))
        # a stays live, b dies. sig-ab (needs a AND b) is dropped; sig-ac (a,c)
        # kept only if BOTH a,c live -> c also dies -> dropped too. Keep only a.
        n.prune(live_sessions={"a"})
        # Both signatures had a dead participant -> both dropped -> both re-notify.
        assert n.should_notify("sig-ab", frozenset({"a", "b"})) is True
        assert n.should_notify("sig-ac", frozenset({"a", "c"})) is True

    def test_prune_keeps_signature_while_all_participants_live(self):
        n = NotifyOnce()
        n.should_notify("sig-ab", frozenset({"a", "b"}))
        n.prune(live_sessions={"a", "b", "c"})  # both still live
        assert n.should_notify("sig-ab", frozenset({"a", "b"})) is False  # not re-notified


class TestSameFileNotifyDecision:
    def test_two_session_file_notifies_once(self):
        n = NotifyOnce()
        sessions = frozenset({"a", "b"})
        sig = samefile_should_notify(
            project_group_id="grp", repo_id="r", repo_rel_path="src/app.py", sessions=sessions, notify_once=n
        )
        assert sig is not None
        # Same pair, same file -> suppressed second time.
        assert (
            samefile_should_notify(
                project_group_id="grp",
                repo_id="r",
                repo_rel_path="src/app.py",
                sessions=sessions,
                notify_once=n,
            )
            is None
        )

    def test_high_churn_by_session_count_never_notifies(self):
        n = NotifyOnce()
        # 3 distinct sessions on one file -> over the contested threshold.
        assert (
            samefile_should_notify(
                project_group_id="grp",
                repo_id="r",
                repo_rel_path="src/app.py",
                sessions=frozenset({"a", "b", "c"}),
                notify_once=n,
            )
            is None
        )

    def test_high_churn_basename_never_notifies(self):
        n = NotifyOnce()
        assert (
            samefile_should_notify(
                project_group_id="grp",
                repo_id="r",
                repo_rel_path="pkg/__init__.py",
                sessions=frozenset({"a", "b"}),
                notify_once=n,
            )
            is None
        )

    def test_new_third_session_is_a_new_signature(self):
        # A pair notifies; a genuinely different 2-set (a joins c) is distinct
        # and notifies once — but a 3-set is suppressed by churn (above). So the
        # signature-vs-churn interaction: {a,b} notifies, {a,c} notifies.
        n = NotifyOnce()
        assert samefile_should_notify(
            project_group_id="grp", repo_id="r", repo_rel_path="f.py", sessions=frozenset({"a", "b"}), notify_once=n
        )
        assert samefile_should_notify(
            project_group_id="grp", repo_id="r", repo_rel_path="f.py", sessions=frozenset({"a", "c"}), notify_once=n
        )

    def test_same_relpath_different_repos_both_notify(self):
        # The SAME repo_rel_path in two DIFFERENT repos is a distinct collision;
        # repo_id in the signature keeps the second from being deduped away.
        n = NotifyOnce()
        s = frozenset({"a", "b"})
        assert samefile_should_notify(
            project_group_id="grp", repo_id="repo-1", repo_rel_path="x.py", sessions=s, notify_once=n
        )
        assert samefile_should_notify(
            project_group_id="grp", repo_id="repo-2", repo_rel_path="x.py", sessions=s, notify_once=n
        )


class TestSameWorktreeNotifyDecision:
    def test_notifies_by_default_and_dedupes(self):
        n = NotifyOnce()
        sessions = frozenset({"a", "b"})
        assert sameworktree_should_notify(
            project_group_id="grp", worktree_root="/wt", sessions=sessions, notify_once=n
        )
        assert (
            sameworktree_should_notify(
                project_group_id="grp", worktree_root="/wt", sessions=sessions, notify_once=n
            )
            is None
        )

    def test_no_churn_suppression_for_worktree(self):
        # Even 3+ sessions on a shared tree still notify (no churn threshold) —
        # a live filesystem race with more sessions is MORE severe, not less.
        n = NotifyOnce()
        assert sameworktree_should_notify(
            project_group_id="grp",
            worktree_root="/wt",
            sessions=frozenset({"a", "b", "c"}),
            notify_once=n,
        )


class TestNotificationBody:
    def test_body_carries_opaque_id_not_name_or_paths(self):
        body = notification_body(
            project_group_id="grp-opaque-123", signal="same-worktree", session_count=2
        )
        assert "grp-opaque-123" in body
        # No file path / worktree path leaks into the unscoped notifications file.
        assert "/" not in body.replace("filesystem", "")  # no path separators
        assert "worktree" in body  # describes the hazard class, not a path

    def test_samefile_body_wording(self):
        body = notification_body(
            project_group_id="grp", signal="same-file", session_count=2
        )
        assert "same file" in body or "merge conflict" in body

    def test_body_count_matches_session_count(self):
        # The body must not hardcode "two" — a 3-session collision says "3".
        body = notification_body(
            project_group_id="grp", signal="same-worktree", session_count=3
        )
        assert "3 sessions" in body
        assert "two" not in body.lower()
