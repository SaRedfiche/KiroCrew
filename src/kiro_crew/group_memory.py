"""Project-group shared memory (design §12.6).

A memory tier keyed by a chat slot's ``project_group_id``: durable,
human/coordinator-authored context that is auto-injected into every session
tagged into the same project group, so project background is maintained ONCE
instead of re-fed per session.

Deliberately a SEPARATE store addressed *by* the group id, not a field on the
slot or the ProjectStore record — the tag stays the only group identity (same
single-source discipline as the coordinator/work-ledger derivations). Read at
turn assembly by :meth:`context.SessionContextBuilder.build_session_context`
between the global-memory and session-lessons tiers; written by the human and by
a coordinator session through the gateway MCP tools.

Storage mirrors :mod:`kiro_crew.work_ledger`: a per-call root under
``data_home()`` (never cached at import — ``data_home()`` is test-overridable),
one directory per group id guarded by :func:`resolved_within` against path
traversal. A missing store is the first-run / no-memory-yet case and yields the
empty string, contributing nothing to the turn (no marker, no error).
"""

from __future__ import annotations

from pathlib import Path

from kiro_crew.config.paths import data_home
from kiro_crew.session_ledger import _store_name, resolved_within

#: Default injection budget (chars) for the group-memory tier. Window-scaled by
#: the caller via the existing caps machinery; this is the unscaled ceiling and
#: mirrors the ``projects`` preference-tier default.
GROUP_MEMORY_CAP = 4_000

_MEMO_FILE = "memory.md"
_TRUNC_MARKER = "\n…[truncated]"


class GroupMemoryError(Exception):
    """Raised for an unusable group id (shape / path-traversal)."""


def _group_memory_root() -> Path:
    """Resolved per call, never cached at import: ``data_home()`` is overridable
    and a module-level constant would freeze the first value a test set."""
    return data_home() / "group-memory"


def _group_id_is_shaped(group_id: str) -> bool:
    """Whether *group_id* has the shape a store path may be built from.

    A group id names a directory, so a null byte or a path separator would let it
    escape its own directory. Mirrors ``work_ledger._slot_key_is_shaped``.
    """
    return (
        bool(group_id)
        and "\0" not in group_id
        and "/" not in group_id
        and "\\" not in group_id
    )


def group_dir(group_id: str) -> Path:
    """The directory holding *group_id*'s shared memory. Does not create it."""
    if not _group_id_is_shaped(group_id):
        raise GroupMemoryError(f"invalid project group id: {group_id!r}")
    resolved = resolved_within(_group_memory_root(), _store_name(group_id))
    if resolved is None:
        raise GroupMemoryError(f"path traversal blocked for group id: {group_id!r}")
    return resolved


def _memo_path(group_id: str) -> Path:
    return group_dir(group_id) / _MEMO_FILE


class GroupMemoryStore:
    """Read/write the shared memory for one project group.

    Keyed by ``project_group_id``. The stored form is a single human/coordinator
    authored markdown blob (``memory.md``); reads are lock-free and torn-file
    safe (a partial write reads as fewer bytes, never a crash).
    """

    def __init__(self, group_id: str):
        self._group_id = group_id
        # Validate the id shape eagerly so a malformed id fails loud at
        # construction rather than silently reading/writing nothing.
        self._path = _memo_path(group_id)

    def read(self) -> str:
        """The raw stored memory text, or ``""`` when nothing is stored yet."""
        try:
            return self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            return ""

    def write(self, text: str) -> None:
        """Replace the group's shared memory with *text* (atomic rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._path)

    def get_context(self, cap: int = GROUP_MEMORY_CAP, *, query: str = "") -> str:
        """A delimited block for prompt injection, or ``""`` when empty.

        ``query`` is accepted for signature parity with the other tiers'
        ``get_context`` (the blob is not query-ranked). ``cap`` bounds the
        injected length; overflow is truncated with a visible marker so the
        model is never handed a silently clipped document.
        """
        text = self.read().strip()
        if not text:
            return ""
        if cap > 0 and len(text) > cap:
            text = text[: max(0, cap - len(_TRUNC_MARKER))] + _TRUNC_MARKER
        return (
            "[PROJECT GROUP MEMORY — shared across every session in this project. "
            "Authored by you / the coordinator; treat as durable project context.]\n"
            f"{text}\n"
            "[END OF PROJECT GROUP MEMORY]"
        )


def delete_group_memory(group_id: str) -> bool:
    """Remove a group's entire shared-memory store. Returns whether it existed.

    Called by the dangling-tag GC when the last member is untagged / the group
    is deleted (design §12.6 GC, piggybacking the §11.4 sweep). Best-effort and
    idempotent — a missing store is a no-op.
    """
    try:
        d = group_dir(group_id)
    except GroupMemoryError:
        return False
    if not d.exists():
        return False
    import shutil

    shutil.rmtree(d, ignore_errors=True)
    return True
