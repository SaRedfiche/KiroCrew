"""Group shared-memory routes (design §12.6) — one test per contract clause.

Pins the security contract the ``group_memory_read`` / ``group_memory_write``
tools rest on: the group a call touches is resolved from the CALLING SESSION's
own slot (never a request arg); any tagged member may read; only a coordinator
(a session owning a work ledger) may write; a member write is refused; an
untagged session gets ``not_tagged``; a cookie caller gets
``internal_auth_required``; and append vs replace behave as documented.

Mirrors ``test_work_ledger_tools.py``'s fixture shape (isolated home, bypassed
recognition, a real-shaped slot table).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from kiro_crew import group_memory, work_ledger
from kiro_crew.dashboard.handlers import group_memory as routes

pytestmark = pytest.mark.asyncio

GROUP = "grp-abc123"
COORD = "dashboard:chat-coord"
MEMBER = "dashboard:chat-member"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test writes into its own data home, never the live one."""
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    yield


@pytest.fixture(autouse=True)
def _open_route(monkeypatch):
    """Bypass session recognition/restriction (their own suites cover them)."""

    async def _recognized(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(routes, "_recognize_session", _recognized)
    monkeypatch.setattr(routes, "_is_restricted_session", lambda *a: False)
    monkeypatch.setattr(routes, "_blocks_reads_session", lambda *a: False)


class _Slot:
    """A slot carrying what the routes + effective_session_key read.

    ``effective_session_key`` reads ``linked_session_key`` (empty for a
    dashboard slot) then ``_history_key_for(slot.key)``; the routes read
    ``project_group_id``.
    """

    def __init__(self, key: str = "", project_group_id: str = "") -> None:
        self.key = key
        self.linked_session_key = ""
        self.project_group_id = project_group_id


_SLOTS: dict[str, _Slot] = {}


@pytest.fixture(autouse=True)
def _clean_slots():
    _SLOTS.clear()
    yield
    _SLOTS.clear()


def _tag(session_key: str, group_id: str) -> None:
    """Register a slot for *session_key* tagged into *group_id*.

    ``effective_session_key`` prefixes a bare slot key with ``dashboard:``; the
    handler resolves the slot by both the raw key and its stripped tail, so the
    table is keyed on the stripped slot name to match ``get_slot`` lookups.
    """
    slot_name = session_key.split(":", 1)[-1] if ":" in session_key else session_key
    _SLOTS[slot_name] = _Slot(key=slot_name, project_group_id=group_id)


def _req(method: str, *, body: Any = ..., sk: str, internal: bool = True) -> web.Request:
    app = web.Application()
    state = MagicMock()
    state.get_slot = MagicMock(side_effect=lambda key: _SLOTS.get(key))
    state._slots = _SLOTS
    app["state"] = state
    req = make_mocked_request(method, "/api/group-memory", app=app, headers={"X-Session-Key": sk})
    req["internal_auth"] = internal
    if body is not ...:
        req.json = AsyncMock(return_value=body)  # type: ignore[method-assign]
    return req


def _coord_effective() -> str:
    """The effective key COORD's slot resolves to (dashboard: + bare slot)."""
    from kiro_crew.dashboard.chat_utils import effective_session_key

    slot_name = COORD.split(":", 1)[-1]
    return effective_session_key(_SLOTS[slot_name])


# ── read: any tagged member ────────────────────────────────────────────────


async def test_member_reads_empty_when_nothing_stored():
    _tag(MEMBER, GROUP)
    resp = await routes.api_group_memory_get(_req("GET", sk=MEMBER))
    assert resp.status == 200
    body = resp.body.decode()
    assert '"memory": ""' in body or '"memory":""' in body


async def test_member_reads_what_a_coordinator_wrote():
    _tag(COORD, GROUP)
    group_memory.GroupMemoryStore(GROUP).write("use us-west-2 for the lab")
    _tag(MEMBER, GROUP)
    resp = await routes.api_group_memory_get(_req("GET", sk=MEMBER))
    assert resp.status == 200
    assert "us-west-2" in resp.body.decode()


async def test_read_returns_503_when_blob_unreadable(monkeypatch):
    # An unreadable blob is a real failure, not a truthful "empty": the read
    # handler must 503 read_failed, NOT report HTTP 200 empty (status artefact).
    _tag(MEMBER, GROUP)
    group_memory.GroupMemoryStore(GROUP).write("stored")

    def _boom(*a, **k):
        raise PermissionError("simulated unreadable memory.md")

    monkeypatch.setattr("pathlib.Path.read_text", _boom)
    resp = await routes.api_group_memory_get(_req("GET", sk=MEMBER))
    assert resp.status == 503
    assert b"read_failed" in resp.body


# ── write: coordinator only ────────────────────────────────────────────────


async def test_coordinator_write_then_read_roundtrips():
    _tag(COORD, GROUP)
    work_ledger.ensure_conductor(_coord_effective(), depth=0, parent_item=None)
    resp = await routes.api_group_memory_write(
        _req("POST", body={"text": "prod is us-east-1"}, sk=COORD)
    )
    assert resp.status == 200, resp.body.decode()
    assert group_memory.GroupMemoryStore(GROUP).read().strip().endswith("prod is us-east-1")


async def test_member_write_is_refused_not_coordinator():
    _tag(MEMBER, GROUP)  # a member, no conductor ledger
    resp = await routes.api_group_memory_write(
        _req("POST", body={"text": "sneaky"}, sk=MEMBER)
    )
    assert resp.status == 403
    assert b"not_coordinator" in resp.body


async def test_append_keeps_prior_and_replace_overwrites():
    _tag(COORD, GROUP)
    work_ledger.ensure_conductor(_coord_effective(), depth=0, parent_item=None)
    await routes.api_group_memory_write(_req("POST", body={"text": "first"}, sk=COORD))
    await routes.api_group_memory_write(
        _req("POST", body={"text": "second", "mode": "append"}, sk=COORD)
    )
    stored = group_memory.GroupMemoryStore(GROUP).read()
    # Exact separator form, not just substring presence — a no-separator
    # concatenation ("firstsecond") would satisfy `in` but is a regression.
    assert stored == "first\n\nsecond"
    await routes.api_group_memory_write(
        _req("POST", body={"text": "only", "mode": "replace"}, sk=COORD)
    )
    stored = group_memory.GroupMemoryStore(GROUP).read()
    assert stored == "only"
    assert "first" not in stored


async def test_append_past_blob_cap_is_400(monkeypatch):
    # An append whose result exceeds the store's total-blob cap is refused with
    # a distinct blob_too_large 400 (not invalid_group), and the prior blob
    # survives.
    monkeypatch.setattr(group_memory, "GROUP_MEMORY_BLOB_MAX", 20)
    _tag(COORD, GROUP)
    work_ledger.ensure_conductor(_coord_effective(), depth=0, parent_item=None)
    await routes.api_group_memory_write(_req("POST", body={"text": "x" * 15}, sk=COORD))
    resp = await routes.api_group_memory_write(
        _req("POST", body={"text": "y" * 15, "mode": "append"}, sk=COORD)
    )
    assert resp.status == 400
    assert b"blob_too_large" in resp.body
    assert group_memory.GroupMemoryStore(GROUP).read() == "x" * 15


# ── refusals ────────────────────────────────────────────────────────────────


async def test_untagged_session_is_refused_not_tagged():
    _SLOTS["chat-member"] = _Slot(project_group_id="")  # open but untagged
    resp = await routes.api_group_memory_get(_req("GET", sk=MEMBER))
    assert resp.status == 404
    assert b"not_tagged" in resp.body


async def test_unknown_session_is_refused():
    # No slot registered at all.
    resp = await routes.api_group_memory_get(_req("GET", sk="dashboard:chat-ghost"))
    assert resp.status == 404
    assert b"unknown_session" in resp.body


async def test_cookie_caller_is_refused_internal_auth():
    _tag(MEMBER, GROUP)
    resp = await routes.api_group_memory_get(_req("GET", sk=MEMBER, internal=False))
    assert resp.status == 403
    assert b"internal_auth_required" in resp.body


async def test_write_group_resolved_from_slot_not_body():
    """A write cannot name another group: there is no project_group_id parameter.

    A stray ``project_group_id`` in the body is an UNKNOWN field and is refused
    outright — a stronger guarantee than silently dropping it — so neither the
    caller's own group nor the named one is written on that call.
    """
    _tag(COORD, GROUP)
    work_ledger.ensure_conductor(_coord_effective(), depth=0, parent_item=None)
    resp = await routes.api_group_memory_write(
        _req("POST", body={"text": "x", "project_group_id": "grp-other"}, sk=COORD)
    )
    assert resp.status == 400
    assert b"invalid_value" in resp.body
    # Nothing was written to either group.
    assert group_memory.GroupMemoryStore(GROUP).read() == ""
    assert group_memory.GroupMemoryStore("grp-other").read() == ""

    # And a clean write (no stray field) lands only on the caller's own group.
    resp = await routes.api_group_memory_write(_req("POST", body={"text": "x"}, sk=COORD))
    assert resp.status == 200
    assert group_memory.GroupMemoryStore(GROUP).read().strip() == "x"
    assert group_memory.GroupMemoryStore("grp-other").read() == ""
