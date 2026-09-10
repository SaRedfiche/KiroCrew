"""Integration test for the Signal-1 per-turn flush (_flush_collision_writes)."""

from __future__ import annotations

import subprocess
import types

import pytest

from kiro_crew.dashboard.chat_runner import _flush_collision_writes
from kiro_crew.dashboard.collision_index import CollisionIndex, FileKey
from kiro_crew.dashboard.collision_notify import NotifyOnce
from kiro_crew.dashboard.worktree_index import WorktreeIndex


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _skip_without_git():
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")


def _repo(tmp_path, remote="git@github.com:org/repo.git"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "remote", "add", "origin", remote)
    return tmp_path


def _slot(project, project_group_id, writes, *, key="chat-1"):
    # A minimal stand-in with the attributes the flush + effective_session_key read.
    return types.SimpleNamespace(
        project=str(project),
        project_group_id=project_group_id,
        _collision_writes=list(writes),
        key=key,
        linked_session_key="",
        forked_from=None,
        title="",
        agent="",
    )


class _CapturingBus:
    def __init__(self):
        self.pushed = []

    def push(self, payload):
        self.pushed.append(payload)
        return {}


def _state(*slots):
    st = types.SimpleNamespace(
        collisions=CollisionIndex(),
        worktrees=WorktreeIndex(),
        collision_notify_once=NotifyOnce(),
        notification_bus=_CapturingBus(),
    )
    st._slots = {s.key: s for s in slots}
    return st



class TestSignal2Notify:
    @pytest.mark.asyncio
    async def test_same_worktree_collision_notifies(self, tmp_path):
        """Two live sessions tagged into one project sharing a worktree ->
        the flush fires a same-worktree notification (default-notify)."""
        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        slot_a = _slot(repo, "grp-1", [str(f)], key="chat-a")
        slot_b = _slot(repo, "grp-1", [], key="chat-b")
        state = _state(slot_a, slot_b)
        # Pre-place B on the same worktree (as if B's flush ran first), under
        # B's EFFECTIVE session key (what the flush + live set use).
        import os as _os

        from kiro_crew.dashboard.chat_utils import effective_session_key

        state.worktrees.set_worktree(
            effective_session_key(slot_b), _os.path.realpath(str(repo))
        )
        await _flush_collision_writes(state, slot_a, effective_session_key(slot_a))
        # A same-worktree notification was pushed, with the opaque project id.
        assert len(state.notification_bus.pushed) == 1
        body = state.notification_bus.pushed[0].body
        assert "grp-1" in body and "worktree" in body
        # Dedupe: a second identical flush does NOT re-notify.
        slot_a._collision_writes = [str(f)]
        await _flush_collision_writes(state, slot_a, effective_session_key(slot_a))
        assert len(state.notification_bus.pushed) == 1

    @pytest.mark.asyncio
    async def test_no_write_turn_still_evaluates_same_worktree(self, tmp_path):
        """A tagged session that shares a tree but wrote nothing this turn still
        notifies (Signal 2 evaluates on an empty write set; GPT-review)."""
        import os as _os

        from kiro_crew.dashboard.chat_utils import effective_session_key

        repo = _repo(tmp_path / "r")
        a = _slot(repo, "grp-1", [], key="chat-a")  # NO writes this turn
        b = _slot(repo, "grp-1", [], key="chat-b")
        state = _state(a, b)
        state.worktrees.set_worktree(effective_session_key(b), _os.path.realpath(str(repo)))
        await _flush_collision_writes(state, a, effective_session_key(a))
        assert len(state.notification_bus.pushed) == 1
        assert "worktree" in state.notification_bus.pushed[0].body

    @pytest.mark.asyncio
    async def test_solo_session_does_not_notify(self, tmp_path):
        from kiro_crew.dashboard.chat_utils import effective_session_key

        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        slot_a = _slot(repo, "grp-1", [str(f)], key="chat-a")
        state = _state(slot_a)  # only one live session
        await _flush_collision_writes(state, slot_a, effective_session_key(slot_a))
        assert state.notification_bus.pushed == []

    @pytest.mark.asyncio
    async def test_fork_pair_on_shared_tree_does_not_notify(self, tmp_path):
        import os as _os

        from kiro_crew.dashboard.chat_utils import effective_session_key

        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        parent = _slot(repo, "grp-1", [str(f)], key="chat-parent")
        child = _slot(repo, "grp-1", [], key="chat-child")
        # forked_from stores the parent's EFFECTIVE session key (see chat_fork.py).
        child.forked_from = effective_session_key(parent)
        state = _state(parent, child)
        state.worktrees.set_worktree(
            effective_session_key(child), _os.path.realpath(str(repo))
        )
        await _flush_collision_writes(state, parent, effective_session_key(parent))
        # A fork pair momentarily sharing the parent's tree is not the hazard.
        assert state.notification_bus.pushed == []

    @pytest.mark.asyncio
    async def test_stranger_breaks_fork_pair_and_notifies(self, tmp_path):
        """Parent + its fork + an UNRELATED stranger on one tree IS a real race
        (the fork exclusion must not swallow it). Drives _is_fork_pair from the
        live-slot forked_from map end-to-end."""
        import os as _os

        from kiro_crew.dashboard.chat_utils import effective_session_key

        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        parent = _slot(repo, "grp-1", [str(f)], key="chat-parent")
        child = _slot(repo, "grp-1", [], key="chat-child")
        stranger = _slot(repo, "grp-1", [], key="chat-stranger")
        child.forked_from = effective_session_key(parent)
        state = _state(parent, child, stranger)
        wt = _os.path.realpath(str(repo))
        state.worktrees.set_worktree(effective_session_key(child), wt)
        state.worktrees.set_worktree(effective_session_key(stranger), wt)
        await _flush_collision_writes(state, parent, effective_session_key(parent))
        assert len(state.notification_bus.pushed) == 1
        assert "worktree" in state.notification_bus.pushed[0].body

    @pytest.mark.asyncio
    async def test_same_tree_that_is_also_same_file_emits_one_note(self, tmp_path):
        """Two sessions sharing a worktree AND contesting the same file this
        turn -> exactly ONE note (same-worktree), not two (per-tree dedupe)."""
        import os as _os

        from kiro_crew.dashboard.chat_utils import effective_session_key

        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        slot_a = _slot(repo, "grp-1", [str(f)], key="chat-a")
        slot_b = _slot(repo, "grp-1", [], key="chat-b")
        state = _state(slot_a, slot_b)
        wt = _os.path.realpath(str(repo))
        # B already on the same tree AND already recorded editing the same file.
        state.worktrees.set_worktree(effective_session_key(slot_b), wt)
        state.collisions.record_edit(
            project_group_id="grp-1",
            repo_id="github.com/org/repo",
            repo_rel_path="a.py",
            session=effective_session_key(slot_b),
        )
        await _flush_collision_writes(state, slot_a, effective_session_key(slot_a))
        # Exactly one note, and it is the same-worktree (higher-severity) one.
        assert len(state.notification_bus.pushed) == 1
        assert "worktree" in state.notification_bus.pushed[0].body


class TestFlush:
    async def _run(self, coro):
        return await coro

    @pytest.mark.asyncio
    async def test_flush_records_edit_and_clears(self, tmp_path):
        repo = _repo(tmp_path / "r")
        f = repo / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        state = _state()
        slot = _slot(repo, "grp-1", [str(f)])

        await _flush_collision_writes(state, slot, "sess-a")

        # Accumulator drained.
        assert slot._collision_writes == []
        # A second session editing the same repo-relative path now collides.
        state.collisions.record_edit(
            project_group_id="grp-1",
            repo_id="github.com/org/repo",
            repo_rel_path="src/app.py",
            session="sess-b",
        )
        hits = state.collisions.contested_files(
            "grp-1", live_sessions={"sess-a", "sess-b"}
        )
        assert len(hits) == 1
        assert hits[0][0] == FileKey("grp-1", "github.com/org/repo", "src/app.py")

    @pytest.mark.asyncio
    async def test_untagged_session_records_nothing(self, tmp_path):
        repo = _repo(tmp_path / "r")
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        state = _state()
        slot = _slot(repo, "", [str(f)])  # untagged
        await _flush_collision_writes(state, slot, "sess-a")
        assert state.collisions.contested_files("", live_sessions={"sess-a"}) == []

    @pytest.mark.asyncio
    async def test_out_of_tree_write_records_nothing(self, tmp_path):
        repo = _repo(tmp_path / "r")
        outside = tmp_path / "outside.py"
        outside.write_text("x", encoding="utf-8")
        state = _state()
        slot = _slot(repo, "grp-1", [str(outside)])
        await _flush_collision_writes(state, slot, "sess-a")
        # Nothing indexed -> even a second session cannot collide.
        state.collisions.record_edit(
            project_group_id="grp-1",
            repo_id="github.com/org/repo",
            repo_rel_path="outside.py",
            session="sess-b",
        )
        assert state.collisions.contested_files(
            "grp-1", live_sessions={"sess-a", "sess-b"}
        ) == []

    @pytest.mark.asyncio
    async def test_sensitive_path_is_skipped(self, tmp_path, monkeypatch):
        """A write whose path validate_file_path rejects (sensitive) is skipped
        by the flush and never indexed (design §4.2 security drop)."""
        import kiro_crew.dashboard.chat_runner as cr

        repo = _repo(tmp_path / "r")
        secret = repo / "secret.env"
        secret.write_text("x", encoding="utf-8")
        # Force this path to be treated as sensitive.
        monkeypatch.setattr(
            cr, "validate_file_path", lambda p: None if p.endswith("secret.env") else p
        )
        state = _state()
        slot = _slot(repo, "grp-1", [str(secret)])
        await _flush_collision_writes(state, slot, "sess-a")
        # Nothing indexed for the sensitive path.
        state.collisions.record_edit(
            project_group_id="grp-1",
            repo_id="github.com/org/repo",
            repo_rel_path="secret.env",
            session="sess-b",
        )
        assert state.collisions.contested_files(
            "grp-1", live_sessions={"sess-a", "sess-b"}
        ) == []

    @pytest.mark.asyncio
    async def test_flush_prunes_stale_keys_across_all_keys(self, tmp_path, monkeypatch):
        """The flush sweeps window-expired rows on EVERY key, not just the one
        it touches this turn — else keys for files no longer edited leak for the
        process lifetime (GPT-review unbounded-memory BLOCK)."""
        import kiro_crew.dashboard.collision_index as ci

        state = _state()
        # Seed many stale keys directly (as if edited long ago).
        for i in range(50):
            state.collisions.record_edit(
                project_group_id="grp-1",
                repo_id="r",
                repo_rel_path=f"old/{i}.py",
                session="past",
                ts=1000.0,
            )
        assert len(state.collisions._by_key) == 50
        # A later flush (even an untagged/no-op one) must prune all stale keys.
        # Freeze "now" far past the window so every seeded key is expired.
        real_time = ci.time.time
        monkeypatch.setattr(ci.time, "time", lambda: 1000.0 + ci.RECENCY_WINDOW_SECS + 10)
        try:
            slot = _slot(tmp_path, "", [])  # untagged, no paths -> record=False
            await _flush_collision_writes(state, slot, "sess-x")
        finally:
            monkeypatch.setattr(ci.time, "time", real_time)
        assert state.collisions._by_key == {}  # all stale keys swept

    @pytest.mark.asyncio
    async def test_empty_accumulator_is_noop(self, tmp_path):
        state = _state()
        slot = _slot(tmp_path, "grp-1", [])
        await _flush_collision_writes(state, slot, "sess-a")  # must not raise
        assert slot._collision_writes == []
