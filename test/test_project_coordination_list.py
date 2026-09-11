"""Tests for GET /api/projects/coordination (project-coordination record list)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.project_panel import api_projects_coordination_list
from kiro_crew.dashboard.project_store import ProjectStore
from kiro_crew.dashboard.state import DashboardState, _ChatSlot
from kiro_crew.dashboard.handlers_project import api_project_get
from kiro_crew.dashboard.routes import _REGISTRARS


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
    async def test_record_shape_is_id_and_name_only(self, tmp_path):
        # The list payload is {id, name} only — no repos rollup (the per-project
        # repos view lives on the panel route; the pick-list never renders it).
        state = _state(tmp_path)
        state.projects.create_project("Alpha", project_id="grp-1")
        state.projects.observe_repo("grp-1", "repo-x")
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get("/api/projects/coordination")
            data = await resp.json()
            assert data["projects"] == [{"id": "grp-1", "name": "Alpha"}]
            assert "repos" not in data["projects"][0]


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

    @pytest.mark.asyncio
    async def test_app_with_no_tagged_session_sees_nothing(self, tmp_path):
        # An app that owns a live session but has it tagged into NO project
        # (project_group_id == "") sees an empty list, even though projects
        # exist — the strictest confinement case (empty `owned`).
        state = _state(tmp_path)
        state.projects.create_project("Alpha", project_id="grp-1")
        untagged_app_slot = _slot("chat-app", "", app="myapp")
        state._slots = {"chat-app": untagged_app_slot}
        async with TestClient(TestServer(_make_app(state))) as client:
            # Dashboard user still sees it.
            resp = await client.get("/api/projects/coordination")
            assert {p["id"] for p in (await resp.json())["projects"]} == {"grp-1"}
            # The app, owning only an untagged session, sees nothing.
            resp = await client.get(
                "/api/projects/coordination", headers={"X-Session-Key": "chat-app"}
            )
            assert (await resp.json())["projects"] == []

    @pytest.mark.asyncio
    async def test_app_view_drops_project_after_untag(self, tmp_path):
        # A session tagged into grp-owned, then UNTAGGED (project_group_id ""),
        # must drop grp-owned from that app's view — the list filters on the
        # slot's CURRENT tag, so a stale/retagged session cannot keep a project
        # visible to an app that no longer has a session in it.
        state = _state(tmp_path)
        state.projects.create_project("Owned", project_id="grp-owned")
        app_slot = _slot("chat-app", "grp-owned", app="myapp")
        state._slots = {"chat-app": app_slot}
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.get(
                "/api/projects/coordination", headers={"X-Session-Key": "chat-app"}
            )
            assert {p["id"] for p in (await resp.json())["projects"]} == {"grp-owned"}
            # Untag the only owning session.
            app_slot.project_group_id = ""
            resp = await client.get(
                "/api/projects/coordination", headers={"X-Session-Key": "chat-app"}
            )
            assert (await resp.json())["projects"] == []


class TestCoordinationRouteResolution:
    """The static /api/projects/coordination must win over the task-runner's
    dynamic /api/projects/{id}, so `coordination` is never captured as a
    task-runner project id. The two routes are registered in DIFFERENT modules
    (sessions vs connections), and the win depends on aiohttp resolving in
    registration order with sessions registered first."""

    def test_sessions_registrar_precedes_connections(self):
        # The ordering invariant the resolution relies on. If a refactor ever
        # reorders these registrars, the static route could be shadowed.
        names = [name for name, _ in _REGISTRARS]
        assert names.index("sessions") < names.index("connections")

    @pytest.mark.asyncio
    async def test_coordination_resolves_to_list_not_taskrunner(self, tmp_path):
        # Register the two COMPETING routes in the real registration order
        # (coordination from sessions first, then {id} from connections) and
        # confirm aiohttp routes /api/projects/coordination to the list handler
        # — NOT api_project_get treating "coordination" as a project id.
        state = MagicMock(spec=DashboardState)
        state._slots = {}
        state.projects = ProjectStore(base_dir=tmp_path)
        # api_project_get resolves the task runner off the app state; a None
        # runner makes it 404 for any id (including, wrongly, "coordination"
        # if the ordering were broken).
        state.task_runner = None

        app = web.Application()
        app["state"] = state
        app.router.add_get(
            "/api/projects/coordination", api_projects_coordination_list
        )  # sessions (registered first)
        app.router.add_get("/api/projects/{id}", api_project_get)  # connections (later)

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/projects/coordination")
            # The list handler answers 200 {"projects": [...]}; the task-runner
            # handler would 404 (no runner) or return a single-project dict.
            assert resp.status == 200
            body = await resp.json()
            assert "projects" in body  # list shape, not a task-runner project
