"""Tests for GET /api/projects/coordination (project-coordination record list)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.project_panel import api_projects_coordination_list
from kiro_crew.dashboard.project_store import ProjectStore
from kiro_crew.dashboard.state import DashboardState, _ChatSlot


def _make_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_get("/api/projects/coordination", api_projects_coordination_list)
    return app


def _state(tmp_path, slots=()):
    state = MagicMock(spec=DashboardState)
    state._slots = {s.key: s for s in slots}
    state.projects = ProjectStore(base_dir=tmp_path)
    return state


def _slot(key, project_group_id, app=""):
    s = _ChatSlot(key)
    s.project_group_id = project_group_id
    s._app = app
    return s


class TestCoordinationList:
    @pytest.mark.asyncio
    async def test_empty_when_no_projects(self, tmp_path):
        state = _state(tmp_path)
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/coordination")
            assert resp.status == 200
            assert (await resp.json()) == {"projects": []}

    @pytest.mark.asyncio
    async def test_dashboard_user_sees_all_projects(self, tmp_path):
        state = _state(tmp_path)
        state.projects.create_project("Alpha", project_id="grp-1")
        state.projects.create_project("Beta", project_id="grp-2")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/coordination")
            data = await resp.json()
            ids = {p["id"] for p in data["projects"]}
            assert ids == {"grp-1", "grp-2"}
            names = {p["name"] for p in data["projects"]}
            assert names == {"Alpha", "Beta"}

    @pytest.mark.asyncio
    async def test_record_shape_includes_repos(self, tmp_path):
        state = _state(tmp_path)
        rec = state.projects.create_project("Alpha", project_id="grp-1")
        state.projects.observe_repo("grp-1", "repo-x")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/coordination")
            data = await resp.json()
            assert data["projects"] == [
                {"id": "grp-1", "name": "Alpha", "repos": ["repo-x"]}
            ]
            del rec  # created for its side effect on the store


class TestCoordinationListAppOwnership:
    @pytest.mark.asyncio
    async def test_app_sees_only_projects_it_owns_a_session_in(self, tmp_path):
        # An app caller (X-Session-Key names an app-owned slot) sees ONLY the
        # projects one of its live sessions is tagged into — never a project
        # only a dashboard/other-app session is in (App Kit §5.2, no cross-app
        # name leak).
        state = _state(tmp_path)
        state.projects.create_project("Owned", project_id="grp-owned")
        state.projects.create_project("Foreign", project_id="grp-foreign")
        # An app-owned session tagged into grp-owned; a dashboard session in
        # grp-foreign.
        app_slot = _slot("chat-app", "grp-owned", app="myapp")
        dash_slot = _slot("chat-dash", "grp-foreign", app="")
        state._slots = {"chat-app": app_slot, "chat-dash": dash_slot}
        async with TestClient(TestServer(_make_app(state))) as client:
            # No app claim -> dashboard user -> sees both.
            resp = await client.get("/api/projects/coordination")
            all_ids = {p["id"] for p in (await resp.json())["projects"]}
            assert all_ids == {"grp-owned", "grp-foreign"}

            # App claim via X-Session-Key naming the app-owned slot -> sees only
            # grp-owned. (derive_caller_app maps the session key to its _app.)
            resp = await client.get(
                "/api/projects/coordination",
                headers={"X-Session-Key": "chat-app"},
            )
            app_ids = {p["id"] for p in (await resp.json())["projects"]}
            assert app_ids == {"grp-owned"}
            assert "grp-foreign" not in app_ids
