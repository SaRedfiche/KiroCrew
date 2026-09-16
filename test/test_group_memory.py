"""Tests for the project-group shared-memory store (design §12.6)."""

from __future__ import annotations

import pytest

from kiro_crew import group_memory
from kiro_crew.group_memory import (
    GROUP_MEMORY_CAP,
    GroupMemoryError,
    GroupMemoryStore,
    delete_group_memory,
)


@pytest.fixture()
def store_root(tmp_path, monkeypatch):
    """Point the group-memory root at a tmp dir (patch the resolver directly,
    mirroring the work-ledger test convention — avoids the config_dir memo)."""
    root = tmp_path / "group-memory"
    monkeypatch.setattr(group_memory, "_group_memory_root", lambda: root)
    return root


class TestGroupMemoryStore:
    def test_read_empty_when_nothing_stored(self, store_root):
        assert GroupMemoryStore("grp-1").read() == ""

    def test_get_context_empty_when_nothing_stored(self, store_root):
        # First-run / no-memory-yet: contributes nothing to the turn.
        assert GroupMemoryStore("grp-1").get_context() == ""

    def test_write_then_read_roundtrip(self, store_root):
        s = GroupMemoryStore("grp-1")
        s.write("Project background: the widget service owns billing.")
        assert s.read() == "Project background: the widget service owns billing."

    def test_get_context_wraps_in_delimited_block(self, store_root):
        s = GroupMemoryStore("grp-1")
        s.write("shared note")
        ctx = s.get_context()
        assert "PROJECT GROUP MEMORY" in ctx
        assert "shared note" in ctx
        assert ctx.endswith("[END OF PROJECT GROUP MEMORY]")

    def test_get_context_truncates_over_cap(self, store_root):
        s = GroupMemoryStore("grp-1")
        s.write("x" * 500)
        ctx = s.get_context(cap=100)
        assert "…[truncated]" in ctx
        # the injected body stays within the cap (plus the small wrapper)
        assert ctx.count("x") <= 100

    def test_two_groups_are_isolated(self, store_root):
        GroupMemoryStore("grp-1").write("one")
        GroupMemoryStore("grp-2").write("two")
        assert GroupMemoryStore("grp-1").read() == "one"
        assert GroupMemoryStore("grp-2").read() == "two"

    def test_write_is_atomic_leaves_no_tmp(self, store_root):
        s = GroupMemoryStore("grp-1")
        s.write("final")
        d = group_memory.group_dir("grp-1")
        assert not any(p.suffix == ".tmp" for p in d.iterdir())


class TestGroupIdShape:
    @pytest.mark.parametrize("bad", ["", "a/b", "a\\b", "a\0b"])
    def test_malformed_id_raises(self, store_root, bad):
        with pytest.raises(GroupMemoryError):
            GroupMemoryStore(bad)

    def test_traversal_blocked(self, store_root):
        # A `..`-bearing id is shape-rejected by the separator guard before it
        # can escape; either way it must never resolve outside the root.
        with pytest.raises(GroupMemoryError):
            group_memory.group_dir("../escape")


class TestDeleteGroupMemory:
    def test_delete_existing_returns_true(self, store_root):
        GroupMemoryStore("grp-1").write("gone soon")
        assert delete_group_memory("grp-1") is True
        assert GroupMemoryStore("grp-1").read() == ""

    def test_delete_missing_returns_false(self, store_root):
        assert delete_group_memory("never-existed") is False

    def test_delete_malformed_id_returns_false(self, store_root):
        # Best-effort + idempotent: a bad id is a no-op, not a raise.
        assert delete_group_memory("a/b") is False


def test_default_cap_is_positive():
    assert GROUP_MEMORY_CAP > 0
