"""Injection-tier wiring for project-group shared memory (design §12.6).

The store itself is covered by ``test_group_memory``. These tests cover the
tier in :meth:`ContextBuilder.build_session_context`: it fires only when the
session carries a ``project_group_id`` and the memory group is in scope, is
injected AFTER global memory and BEFORE session lessons, honours the
``blocks_reads`` (temporary-session) gate, caps its length, and self-defers to
nothing on a first-run / malformed id.
"""

from __future__ import annotations

import pytest

from kiro_crew import group_memory
from kiro_crew.context import (
    CONTEXT_GROUP_MEMORY,
    ContextBuilder,
    GroupMemoryStore,
)
from kiro_crew.learn import LessonStore
from kiro_crew.memory import MemoryStore
from kiro_crew.skills import SkillsLoader

_GROUP = "grp-abc-123"
_BODY = "The payments service owns the ledger. Do not touch the refund path."


@pytest.fixture()
def group_root(tmp_path, monkeypatch):
    """Point the group-memory store root at a tmp dir (patch the resolver, same
    convention as the store test)."""
    root = tmp_path / "group-memory"
    monkeypatch.setattr(group_memory, "_group_memory_root", lambda: root)
    return root


def _builder(tmp_path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
        lessons=LessonStore(base_dir=tmp_path),
    )


class TestGroupMemoryTier:
    def test_injected_when_tagged_and_memory_present(self, tmp_path, group_root):
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = _builder(tmp_path).build_session_context(project_group_id=_GROUP)
        assert "[PROJECT GROUP MEMORY" in ctx
        assert _BODY in ctx
        assert "[END OF PROJECT GROUP MEMORY]" in ctx

    def test_absent_when_no_group_tag(self, tmp_path, group_root):
        # A session with no project_group_id gets no tier, even if some other
        # group has memory stored.
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = _builder(tmp_path).build_session_context(project_group_id=None)
        assert "[PROJECT GROUP MEMORY" not in ctx

    def test_absent_when_group_has_no_memory(self, tmp_path, group_root):
        # Tagged, but the group has nothing stored yet: first-run defers to "".
        ctx = _builder(tmp_path).build_session_context(project_group_id=_GROUP)
        assert "[PROJECT GROUP MEMORY" not in ctx

    def test_absent_when_blocks_reads(self, tmp_path, group_root):
        # Temporary sessions block all memory reads — the group tier included.
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = _builder(tmp_path).build_session_context(
            project_group_id=_GROUP, blocks_reads=True
        )
        assert "[PROJECT GROUP MEMORY" not in ctx

    def test_absent_when_memory_group_withheld(self, tmp_path, group_root):
        # A sub-agent whose parent opted OUT of the memory group gets no group
        # tier (it rides the same CONTEXT_GROUP_MEMORY gate as global memory).
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = _builder(tmp_path).build_session_context(
            project_group_id=_GROUP,
            context_groups=frozenset(),  # nothing included
        )
        assert "[PROJECT GROUP MEMORY" not in ctx

    def test_injected_when_memory_group_included(self, tmp_path, group_root):
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = _builder(tmp_path).build_session_context(
            project_group_id=_GROUP,
            context_groups=frozenset({CONTEXT_GROUP_MEMORY}),
        )
        assert "[PROJECT GROUP MEMORY" in ctx

    def test_precedence_after_global_memory_before_lessons(self, tmp_path, group_root):
        # The tier sits AFTER the global-memory block and BEFORE the lessons
        # block. Seed a global-memory preference, a lesson, and group memory,
        # then assert BOTH ordering halves by index.
        from kiro_crew.learn import Lesson

        builder = _builder(tmp_path)
        builder.memory.init()
        builder.memory.add_preference("Prefer small, granular commits.")
        builder.lessons.save(
            Lesson(
                ts="2026-01-01T00:00:00Z",
                rule="Always branch before editing source.",
                category="preference",
            )
        )
        GroupMemoryStore(_GROUP).write(_BODY)
        ctx = builder.build_session_context(project_group_id=_GROUP)
        gi = ctx.index("[PROJECT GROUP MEMORY")
        # Global memory block must render BEFORE the group tier (the untested
        # half the original test omitted — precedence-on-conflict wants session/
        # global memory to outrank shared project context).
        mi = ctx.find("[Memory")
        assert mi != -1, "expected the seeded preference to render a memory block"
        assert mi < gi, "global memory must be injected before group memory"
        # And the group tier before session lessons.
        li = ctx.find("[Learned corrections")
        assert li != -1, "expected the seeded lesson to render a lessons block"
        assert gi < li, "group memory must be injected before session lessons"

    def test_malformed_group_id_defers_without_raising(self, tmp_path, group_root):
        # A path-separator id is unusable; the tier logs+skips rather than
        # failing the whole context build.
        ctx = _builder(tmp_path).build_session_context(project_group_id="a/b")
        assert "[PROJECT GROUP MEMORY" not in ctx
        assert "[CRITICAL RULES" in ctx  # the build still completed

    def test_cap_truncates_overlong_memory(self, tmp_path, group_root):
        GroupMemoryStore(_GROUP).write("X" * 100_000)
        ctx = _builder(tmp_path).build_session_context(project_group_id=_GROUP)
        assert "[PROJECT GROUP MEMORY" in ctx
        assert "…[truncated]" in ctx
