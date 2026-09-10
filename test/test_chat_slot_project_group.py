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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": 5},
                )
                assert resp.status == 400
                assert (await resp.json())["code"] == "bad_project_group_id"

    @pytest.mark.asyncio
    async def test_blank_name_is_400_not_untag(self, tmp_path):
        """A present but whitespace-only `name` is a malformed CREATE -> 400,
        NOT a silent untag. Untag is the name-ABSENT path; treating a blank name
        as untag would erase an existing tag (GPT-review data-loss BLOCK)."""
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-keep"  # already tagged
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json={"name": "   "}
                )
                assert resp.status == 400
                assert (await resp.json())["code"] == "empty_name"
                # The existing tag is NOT erased.
                assert slot.project_group_id == "grp-keep"

    @pytest.mark.asyncio
    async def test_save_refusal_attach_rolls_back_and_409(self, tmp_path):
        """Attach-existing path: if save_slot_off_loop refuses, the field rolls
        back to its prior value and the response is 409."""
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

    @pytest.mark.asyncio
    async def test_rollback_is_guarded_not_unconditional(self, tmp_path):
        """The rollback only fires while the field still holds THIS request's
        value: if a concurrent writer set a THIRD value during the (refused)
        save, the rollback must NOT clobber it. Kills a mutation that drops the
        `if slot.project_group_id == project_group_id` guard."""
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-prior"
        state = _mock_state(tmp_path, slot)
        state.projects.create_project("Target", project_id="grp-new")

        async def _refuse_after_third_party_write(*_a, **_k):
            # Simulate a concurrent writer landing a different value before the
            # save returns its refusal.
            slot.project_group_id = "grp-concurrent"
            return False

        with patch(
            "kiro_crew.dashboard.chat_folders.save_slot_off_loop",
            side_effect=_refuse_after_third_party_write,
        ):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": "grp-new"},
                )
                assert resp.status == 409
                # Guarded rollback: the concurrent value is preserved, NOT
                # overwritten back to grp-prior.
                assert slot.project_group_id == "grp-concurrent"

    @pytest.mark.asyncio
    async def test_create_then_save_refusal_leaves_no_orphan_record(self, tmp_path):
        """CREATE path: if the save refuses (409), the project record must NOT
        be stranded in the store — create_project is committed inside the lock
        only after the re-check, and a refused save means no record was created
        (Correctness-review HIGH: orphan project record). Here the slot is not
        rebound, so the re-check passes and create runs; the save then refuses,
        and the record must be gone (rolled back with the tag)."""
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch(
            "kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=False
        ):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group", json={"name": "Orphan"}
                )
                assert resp.status == 409
                # No orphan: the create was committed under the same lock, and a
                # refused save leaves the store without the stranded record.
                assert state.projects.list_projects() == []
                assert slot.project_group_id == ""

    @pytest.mark.asyncio
    async def test_rebind_before_lock_is_409_no_create_no_mutate(self, tmp_path):
        """If the slot is swapped out from under the request before the in-lock
        re-check, the handler returns 409, does NOT create a project, and does
        NOT mutate the (new) slot. Covers the _slot_meta_txn_lock rebind path."""
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)

        # save_slot_off_loop must never be reached on this path.
        save_mock = MagicMock()

        async def _swap_slot(*_a, **_k):
            # Rebind: a different object now lives under the same name, so the
            # in-lock identity re-check (state._slots.get(name) is not slot) trips.
            state._slots["test"] = _ChatSlot("test")
            return {"name": "X"}, None  # (body, err) contract of read_bounded_json

        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", save_mock):
            with patch(
                "kiro_crew.dashboard.chat_folders.read_bounded_json",
                side_effect=_swap_slot,
            ):
                async with TestClient(TestServer(_make_app(state))) as client:
                    resp = await client.post(
                        "/api/chat/slots/test/project-group", json={"name": "X"}
                    )
                    assert resp.status == 409
                    assert (await resp.json())["code"] == "session_gone"
                    save_mock.assert_not_called()
                    assert state.projects.list_projects() == []  # no create

    @pytest.mark.asyncio
    async def test_null_name_is_400_not_untag(self, tmp_path):
        """{"name": null} (key present, JSON null) is NOT the omit-to-untag
        signal — it must be 400 bad_name, never a silent tag erase (GPT-review
        data-loss). Same for a present null project_group_id."""
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-keep"
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                r1 = await client.post(
                    "/api/chat/slots/test/project-group", json={"name": None}
                )
                assert r1.status == 400
                assert (await r1.json())["code"] == "bad_name"
                r2 = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": None},
                )
                assert r2.status == 400
                assert (await r2.json())["code"] == "bad_project_group_id"
                # Neither erased the existing tag.
                assert slot.project_group_id == "grp-keep"

    @pytest.mark.asyncio
    async def test_explicit_empty_id_untags(self, tmp_path):
        """An explicit {"project_group_id": ""} is a valid untag (key present,
        string, empty) — distinct from a present null."""
        slot = _ChatSlot("test")
        slot.project_group_id = "grp-old"
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    json={"project_group_id": ""},
                )
                assert resp.status == 200
                assert slot.project_group_id == ""

    @pytest.mark.asyncio
    async def test_non_object_body_is_400(self, tmp_path):
        """A valid-but-non-object JSON body ([], 5, "s", true) must be 400
        body_not_object via the shared guard, never a 500 from .get() (#5587)."""
        slot = _ChatSlot("test")
        state = _mock_state(tmp_path, slot)
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
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
        with patch("kiro_crew.dashboard.chat_folders.save_slot_off_loop", return_value=True):
            async with TestClient(TestServer(_make_app(state))) as client:
                resp = await client.post(
                    "/api/chat/slots/test/project-group",
                    data="{not json",
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 400
