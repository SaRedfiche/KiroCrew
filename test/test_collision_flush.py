"""Integration test for the Signal-1 per-turn flush (_flush_collision_writes)."""

from __future__ import annotations

import subprocess
import types

import pytest

from kiro_crew.dashboard.chat_runner import _flush_collision_writes
from kiro_crew.dashboard.collision_index import CollisionIndex, FileKey


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


def _slot(project, project_group_id, writes):
    # A minimal stand-in with just the attributes the flush reads.
    return types.SimpleNamespace(
        project=str(project),
        project_group_id=project_group_id,
        _collision_writes=list(writes),
    )


def _state():
    return types.SimpleNamespace(collisions=CollisionIndex())


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
