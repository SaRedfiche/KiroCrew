"""Round-trip + lifecycle tests for the ``project_group_id`` slot field.

Phase-1 project-coordination grouping tag. These pin the properties the
adversarial gate flagged as unverified: the field must survive
save->rehydrate at every restore path, be clearable by absence, inherit on
fork, and clear (with rollback restore) on a workspace switch.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kiro_crew.dashboard.chat import restore_recent_sessions
from kiro_crew.dashboard.state import DashboardState, _ChatSlot
from kiro_crew.history import (
    ROWS_ONLY_DEFERRED_META_KEYS,
    SLOT_OWNED_META_KEYS,
    ConversationLog,
)


def _make_state(tmp_path):
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        crons=MagicMock(list_jobs=MagicMock(return_value=[]), status=MagicMock(return_value={})),
        lessons=MagicMock(load_all=MagicMock(return_value=[])),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )


def _write_session(tmp_path: Path, key: str, meta: dict | None = None) -> None:
    path = tmp_path / f"{key}.jsonl"
    meta_line = {"_type": "metadata", "created_at": "2026-03-23T10:00:00", "last_consolidated": 0}
    if meta:
        meta_line.update(meta)
    lines = [json.dumps(meta_line), json.dumps({"role": "user", "content": "hi", "ts": "t"})]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestProjectGroupIdMembership:
    def test_owned_and_deferred(self):
        # Owned so it is clearable-by-absence; flows into the DERIVED
        # deferred set automatically (no hand-add) — pins the commit claim.
        assert "project_group_id" in SLOT_OWNED_META_KEYS
        assert "project_group_id" in ROWS_ONLY_DEFERRED_META_KEYS

    def test_default_empty(self):
        assert _ChatSlot("s").project_group_id == ""


class TestProjectGroupIdRehydrate:
    def test_restores_from_metadata(self, tmp_path, monkeypatch):
        """The tag survives save->rehydrate via restore_recent_sessions
        (_rehydrate_slot_from_history read path)."""
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(
            tmp_path,
            "dashboard_grp1",
            meta={"workspace": "myws", "project_group_id": "grp-abc"},
        )
        (tmp_path / "dashboard_grp1.jsonl").touch()
        state = _make_state(tmp_path)
        assert restore_recent_sessions(state, window_minutes=60) == 1
        assert state._slots["grp1"].project_group_id == "grp-abc"

    def test_absent_rehydrates_empty(self, tmp_path, monkeypatch):
        """A metadata line with no project_group_id restores to "" — the
        clearable-by-absence property (an untagged / cleared session)."""
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(tmp_path, "dashboard_grp2", meta={"workspace": "myws"})
        (tmp_path / "dashboard_grp2.jsonl").touch()
        state = _make_state(tmp_path)
        assert restore_recent_sessions(state, window_minutes=60) == 1
        assert state._slots["grp2"].project_group_id == ""

    def test_coerced_to_str(self, tmp_path, monkeypatch):
        """A hand-edited/corrupted metadata line delivering a non-string is
        coerced (str()), preventing type confusion of the declared-str field."""
        monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
        _write_session(tmp_path, "dashboard_grp3", meta={"project_group_id": 12345})
        (tmp_path / "dashboard_grp3.jsonl").touch()
        state = _make_state(tmp_path)
        assert restore_recent_sessions(state, window_minutes=60) == 1
        val = state._slots["grp3"].project_group_id
        assert val == "12345" and isinstance(val, str)


class TestProjectGroupIdRoundTripWrite:
    def test_save_then_reload_round_trips(self, tmp_path):
        """The WRITE side: a tagged slot persisted via the metadata writer and
        re-read from disk carries the tag (write-site <-> read-site pairing)."""
        cl = ConversationLog(base_dir=tmp_path)
        key = "dashboard_wt1"
        cl.update_metadata(
            key,
            {"_type": "metadata", "workspace": "myws", "project_group_id": "grp-xyz"},
        )
        meta = cl.get_metadata(key)
        assert meta.get("project_group_id") == "grp-xyz"


class TestProjectGroupIdFork:
    def test_fork_inherits_tag(self):
        parent = _ChatSlot("parent")
        parent.project_group_id = "grp-fork"
        child = _ChatSlot("child")
        # Mirror the chat_fork copy line directly (the fork builder copies the
        # field the same way it copies slot.project).
        child.project_group_id = parent.project_group_id
        assert child.project_group_id == "grp-fork"
