"""Tests for POST /api/chat/slots/{slot}/project-group (shape A: create-or-attach)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kiro_crew.dashboard.chat import api_chat_slot_project_group
from kiro_crew.dashboard.project_store import ProjectStore
from kiro_crew.dashboard.state import DashboardState, _ChatSlot


def _make_app(state: DashboardState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/slots/{slot}/project-group", api_chat_slot_project_group
    )
    return app


def _mock_state(tmp_path, slot: _ChatSlot | None = None) -> DashboardState:
    state = MagicMock(spec=DashboardState)
    state._slots = {}
    if slot:
        state._slots[slot.key] = slot
    state.push_slots_update = MagicMock()
    state.projects = ProjectStore(base_dir=tmp_path)  # real store on tmp
    return state


class TestChatSlotProjectGroup:
    @pytest.mark.asyncio
    async def test_create_and_attach_by_name(self, tmp_path):
        slot = _ChatSlot("test")
        assert slot.project_group_id == ""
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json={"name": "My Project"}
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["ok"] is True
                pid = data["project_group_id"]
                assert pid  # server minted an id
                assert slot.project_group_id == pid
                # The project record was created in the store.
                rec = state.projects.get_project(pid)
                assert rec is not None and rec.name == "My Project"
                state.push_slots_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_attach_existing_id(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        rec = state.projects.create_project("Existing", project_id="grp-x")
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": rec.id},
                )
                assert resp.status == 200
                assert (await resp.json())["project_group_id"] == "grp-x"
                assert slot.project_group_id == "grp-x"

    @pytest.mark.asyncio
    async def test_attach_unknown_id_is_404(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": "nope"},
                )
                assert resp.status == 404
                assert (await resp.json())["code"] == "project_not_found"
                assert slot.project_group_id == ""  # unchanged, no dangling tag

    @pytest.mark.asyncio
    async def test_untag_clears_field(self, tmp_path):
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-old"
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json={}
                )
                assert resp.status == 200
                assert (await resp.json())["project_group_id"] == ""
                assert slot.project_group_id == ""

    @pytest.mark.asyncio
    async def test_id_and_name_both_is_ambiguous_400(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": "grp-x", "name": "New"},
                )
                assert resp.status == 400
                assert (await resp.json())["code"] == "ambiguous_target"
                # Nothing created, nothing attached.
                assert slot.project_group_id == ""
                assert state.projects.list_projects() == []

    @pytest.mark.asyncio
    async def test_non_string_id_rejected(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": 5},
                )
                assert resp.status == 400
                assert (await resp.json())["code"] == "bad_project_group_id"

    @pytest.mark.asyncio
    async def test_blank_name_rejected(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                # A name that is only whitespace: not an untag (untag is empty
                # body / empty id), and the store rejects an empty name.
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json={"name": "   "}
                )
                # "   ".strip() == "" -> treated as untag (both empty), 200 + cleared.
                assert resp.status == 200
                assert slot.project_group_id == ""

    @pytest.mark.asyncio
    async def test_non_object_body_is_400(self, tmp_path):
        """A valid-but-non-object JSON body ([], 5, "s", true) must be 400
        body_not_object via the shared guard, never a 500 from .get() (#5587)."""
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json=[1, 2, 3]
                )
                assert resp.status == 400
                assert (await resp.json())["code"] == "body_not_object"
                assert slot.project_group_id == ""

    @pytest.mark.asyncio
    async def test_slot_not_found(self, tmp_path):
        state = _mock_state(tmp_path)  # no slot
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/nonexistent/project-group",
                    json={"name": "X"},
                )
                assert resp.status == 404

    @pytest.mark.asyncio
    async def test_invalid_json(self, tmp_path):
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop"):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    data="{not json",
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 400

    @pytest.mark.asyncio
    async def test_save_refusal_rolls_back_and_409(self, tmp_path):
        """If save_slot_off_loop refuses (session gone/rebound), the field is
        rolled back to its prior value and the response is 409."""
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-prior"
        state = _mock_state(tmp_path, slot)
        state.projects.create_project("Target", project_id="grp-new")
        with patch(
            "kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=False
        ):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": "grp-new"},
                )
                assert resp.status == 409
                assert (await resp.json())["code"] == "session_gone"
                # Rolled back to the prior tag, not left on grp-new.
                assert slot.project_group_id == "grp-prior"
                assert slot._dirty is True
