"""Tests for GET /api/projects/{id}/panel (project-coordination browse view)."""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.collision_index import CollisionIndex
from kiro_crew.dashboard.project_panel import api_project_panel
from kiro_crew.dashboard.project_store import ProjectStore
from kiro_crew.dashboard.state import DashboardState, _ChatSlot
from kiro_crew.dashboard.worktree_index import WorktreeIndex


def _make_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/projects/{id}/panel", api_project_panel)
    return app


def _state(tmp_path, slots=()):
    state = MagicMock(spec=DashboardState)
    state._slots = {s.key: s for s in slots}
    state.projects = ProjectStore(base_dir=tmp_path)
    state.collisions = CollisionIndex()
    state.worktrees = WorktreeIndex()
    return state


def _slot(key, project_group_id, project="", title="", agent=""):
    s = _ChatSlot(key)
    s.project_group_id = project_group_id
    s.project = project
    s.title = title
    s.agent = agent
    return s


class TestProjectPanel:
    @pytest.mark.asyncio
    async def test_unknown_project_is_404(self, tmp_path):
        state = _state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/nope/panel")
            assert resp.status == 404

    @pytest.mark.asyncio
    async def test_panel_lists_tagged_live_sessions(self, tmp_path):
        state = _state(tmp_path)
        rec = state.projects.create_project("My Project", project_id="grp-1")
        # Two slots: one tagged into grp-1, one untagged.
        tagged = _slot("chat-a", "grp-1", title="Work", agent="kirocrew")
        other = _slot("chat-b", "", title="Elsewhere")
        state._slots = {"chat-a": tagged, "chat-b": other}
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            assert resp.status == 200
            data = await resp.json()
            assert data["project"] == {"id": "grp-1", "name": "My Project", "repos": []}
            sessions = data["sessions"]
            assert len(sessions) == 1  # only the tagged one
            assert sessions[0]["title"] == "Work"
            assert sessions[0]["agent"] == "kirocrew"
            assert data["collisions"] == []  # no edits recorded

    @pytest.mark.asyncio
    async def test_panel_surfaces_samefile_collision_flag(self, tmp_path):
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        a = _slot("chat-a", "grp-1")
        b = _slot("chat-b", "grp-1")
        state._slots = {"chat-a": a, "chat-b": b}
        # Two live sessions contest one file.
        from kiro_crew.dashboard.chat_utils import effective_session_key

        for s in (a, b):
            state.collisions.record_edit(
                project_group_id="grp-1",
                repo_id="r",
                repo_rel_path="src/x.py",
                session=effective_session_key(s),
            )
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            flags = [c for c in data["collisions"] if c["signal"] == "same-file"]
            assert len(flags) == 1
            assert flags[0]["repo_rel_path"] == "src/x.py"

    @pytest.mark.asyncio
    async def test_panel_surfaces_sameworktree_flag(self, tmp_path):
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        a = _slot("chat-a", "grp-1")
        b = _slot("chat-b", "grp-1")
        state._slots = {"chat-a": a, "chat-b": b}
        from kiro_crew.dashboard.chat_utils import effective_session_key

        wt = "/repo/shared"
        state.worktrees.set_worktree(effective_session_key(a), wt)
        state.worktrees.set_worktree(effective_session_key(b), wt)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            flags = [c for c in data["collisions"] if c["signal"] == "same-worktree"]
            assert len(flags) == 1
            # The flag lists sessions but MUST NOT echo the worktree path.
            assert "root" not in flags[0] and "worktree_root" not in flags[0]

    @pytest.mark.asyncio
    async def test_panel_cross_project_worktree_filtered_out(self, tmp_path):
        # A shared tree whose co-tenants are NOT in this project must not surface.
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        mine = _slot("chat-mine", "grp-1")
        x1 = _slot("x1", "grp-other")
        x2 = _slot("x2", "grp-other")
        state._slots = {"chat-mine": mine, "x1": x1, "x2": x2}
        from kiro_crew.dashboard.chat_utils import effective_session_key

        # Two OTHER-project sessions share a tree; mine is not on it.
        state.worktrees.set_worktree(effective_session_key(x1), "/repo/other")
        state.worktrees.set_worktree(effective_session_key(x2), "/repo/other")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            # No same-worktree flag: the shared tree has no grp-1 session on it.
            assert [c for c in data["collisions"] if c["signal"] == "same-worktree"] == []

    @pytest.mark.asyncio
    async def test_panel_samefile_flag_excludes_retagged_session(self, tmp_path):
        # A session edited file x under grp-1, then RETAGGED to grp-2 (still
        # live). grp-1's panel must NOT list that session's id (Security: retag
        # leak) — same-file flags are scoped to sessions currently tagged grp-1.
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        from kiro_crew.dashboard.chat_utils import effective_session_key

        stay = _slot("chat-stay", "grp-1")
        moved = _slot("chat-moved", "grp-2")  # retagged away from grp-1
        state._slots = {"chat-stay": stay, "chat-moved": moved}
        stay_key = effective_session_key(stay)
        moved_key = effective_session_key(moved)
        # Both recorded editing the same file UNDER grp-1 (before the retag).
        for k in (stay_key, moved_key):
            state.collisions.record_edit(
                project_group_id="grp-1", repo_id="r", repo_rel_path="x.py", session=k
            )
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            sf = [c for c in data["collisions"] if c["signal"] == "same-file"]
            # Only one session (stay) is currently tagged grp-1 -> not a 2-session
            # collision, and the moved session's id never appears.
            for c in sf:
                assert moved_key not in c["sessions"]

    @pytest.mark.asyncio
    async def test_panel_worktree_flag_lists_only_own_project_sessions(self, tmp_path):
        # A grp-1 session shares a tree with a grp-other session. The flag shows
        # (there IS a race), but MUST list only grp-1's session id, never the
        # other project's (Security: cross-project session-id leak).
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        mine = _slot("chat-mine", "grp-1")
        theirs = _slot("chat-theirs", "grp-other")
        state._slots = {"chat-mine": mine, "chat-theirs": theirs}
        from kiro_crew.dashboard.chat_utils import effective_session_key

        wt = "/repo/shared"
        mine_key = effective_session_key(mine)
        theirs_key = effective_session_key(theirs)
        state.worktrees.set_worktree(mine_key, wt)
        state.worktrees.set_worktree(theirs_key, wt)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            flags = [c for c in data["collisions"] if c["signal"] == "same-worktree"]
            assert len(flags) == 1
            assert flags[0]["sessions"] == [mine_key]  # NOT theirs_key
            assert theirs_key not in flags[0]["sessions"]

    @pytest.mark.asyncio
    async def test_panel_excludes_fork_pair_from_worktree_flag(self, tmp_path):
        # A fork pair sharing the parent's tree must NOT show as a panel
        # same-worktree collision (parity with the notify path).
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        parent = _slot("chat-parent", "grp-1")
        child = _slot("chat-child", "grp-1")
        from kiro_crew.dashboard.chat_utils import effective_session_key

        child.forked_from = effective_session_key(parent)
        state._slots = {"chat-parent": parent, "chat-child": child}
        state.worktrees.set_worktree(effective_session_key(parent), "/repo/wt")
        state.worktrees.set_worktree(effective_session_key(child), "/repo/wt")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
            assert [c for c in data["collisions"] if c["signal"] == "same-worktree"] == []



class TestProjectPanelWorkRollup:
    """Phase-2 P2.2: the panel DERIVES a work-ledger progress rollup from group
    members' conductor ledgers (no stored coupling). These tests stub the two
    panel-facing ledger reads (``read_conductor`` / ``list_work_items``) so they
    exercise the panel's derivation + non-leak scoping directly; the ledger's own
    persistence is covered by the work-ledger suite."""

    @staticmethod
    def _patch_ledger(monkeypatch, *, conductors, items_by_key):
        """conductors: set of slot keys that ARE conductors.
        items_by_key: {slot_key: [WorkItem, ...]}."""
        from kiro_crew.dashboard import project_panel
        from kiro_crew.work_ledger import ConductorRecord

        def _read_conductor(sk, **_):
            return ConductorRecord(slot_key=sk) if sk in conductors else None

        def _list_work_items(sk):
            return list(items_by_key.get(sk, []))

        monkeypatch.setattr(project_panel.work_ledger, "read_conductor", _read_conductor)
        monkeypatch.setattr(project_panel.work_ledger, "list_work_items", _list_work_items)

    @pytest.mark.asyncio
    async def test_conductor_member_surfaces_items_and_flag(self, tmp_path, monkeypatch):
        from kiro_crew.work_ledger import WorkItem

        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        coord = _slot("chat-coord", "grp-1", title="Coordinator")
        worker = _slot("chat-worker", "grp-1", title="Worker")
        state._slots = {"chat-coord": coord, "chat-worker": worker}

        item = WorkItem(
            item_id="it_abc12345",
            title="Do subtask A",
            state="open",
            status="progress",
            summary="halfway",
            pr=42,
            round=1,
            worker_session_key="chat-worker",
        )
        self._patch_ledger(
            monkeypatch, conductors={"chat-coord"}, items_by_key={"chat-coord": [item]}
        )

        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()

        by_key = {r["session"]: r for r in data["sessions"]}
        assert by_key["dashboard:chat-coord"].get("is_coordinator") is True
        assert "is_coordinator" not in by_key["dashboard:chat-worker"]

        assert len(data["work"]) == 1
        w = data["work"][0]
        assert w["coordinator"] == "dashboard:chat-coord"
        assert w["item_id"] == "it_abc12345"
        assert w["title"] == "Do subtask A"
        assert w["status"] == "progress"
        assert w["summary"] == "halfway"
        assert w["pr"] == 42
        assert w["worker"] == "dashboard:chat-worker"  # effective form of the raw key

    @pytest.mark.asyncio
    async def test_no_ledger_means_empty_work_and_no_flag(self, tmp_path, monkeypatch):
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        plain = _slot("chat-plain", "grp-1")
        state._slots = {"chat-plain": plain}
        # No conductors, no items.
        self._patch_ledger(monkeypatch, conductors=set(), items_by_key={})
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
        assert data["work"] == []
        assert "is_coordinator" not in data["sessions"][0]

    @pytest.mark.asyncio
    async def test_worker_in_another_project_is_not_leaked(self, tmp_path, monkeypatch):
        # An item bound to a worker NOT tagged into this project must surface the
        # item (its coordinator is in-project) but NULL the worker id — same
        # non-leak rule as the collision flags.
        from kiro_crew.work_ledger import WorkItem

        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        coord = _slot("chat-coord", "grp-1")
        foreign = _slot("chat-foreign", "grp-other")  # different project
        state = _state(tmp_path)
        state.projects.create_project("P", project_id="grp-1")
        coord = _slot("chat-coord", "grp-1")
        foreign = _slot("chat-foreign", "grp-other")  # different project
        state._slots = {"chat-coord": coord, "chat-foreign": foreign}
        # coord is a conductor with one item bound to the FOREIGN worker (not a
        # member of grp-1).
        item = WorkItem(item_id="it_ff001122", title="T", state="open",
                        worker_session_key="chat-foreign")
        self._patch_ledger(
            monkeypatch, conductors={"chat-coord"}, items_by_key={"chat-coord": [item]}
        )
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/grp-1/panel")
            data = await resp.json()
        assert len(data["work"]) == 1
        assert data["work"][0]["worker"] is None  # foreign worker id not leaked
