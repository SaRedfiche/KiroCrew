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

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.paths import data_home
from kiro_crew.platform_compat import file_lock
from kiro_crew.session_ledger import _store_name, resolved_within

#: Default injection budget (chars) for the group-memory tier. Window-scaled by
#: the caller via the existing caps machinery; this is the unscaled ceiling and
#: mirrors the ``projects`` preference-tier default.
GROUP_MEMORY_CAP = 4_000

#: Hard ceiling on the whole STORED blob (not just one write). A single write's
#: ``text`` is capped by the schema; append accumulates, so without this the file
#: would grow without bound (and every tagged turn re-reads the whole file before
#: the read-time cap trims it). A write whose result would exceed this is REFUSED
#: (see :meth:`GroupMemoryStore.append`), never silently truncated.
GROUP_MEMORY_BLOB_MAX = 64_000

_MEMO_FILE = "memory.md"
_LOCK_FILE = ".memory.lock"
_TRUNC_MARKER = "\n…[truncated]"
_APPEND_SEPARATOR = "\n\n"


class GroupMemoryError(Exception):
    """Raised for an unusable group id (shape / path-traversal)."""


class GroupMemoryBlobTooLarge(GroupMemoryError):
    """An append would push the stored blob past :data:`GROUP_MEMORY_BLOB_MAX`."""


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
    safe (a partial write reads as fewer bytes, never a crash), and WRITES are
    serialised by a per-group advisory lock so a concurrent append cannot lose an
    entry (two coordinator sessions can write the same group).
    """

    def __init__(self, group_id: str):
        self._group_id = group_id
        # Validate the id shape eagerly so a malformed id fails loud at
        # construction rather than silently reading/writing nothing.
        self._path = _memo_path(group_id)
        self._lock_path = self._path.parent / _LOCK_FILE

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the per-group advisory write lock (mirrors work_ledger._open_lock).

        Opened ``"r+"`` without truncation (``msvcrt.locking`` needs a writable
        handle; ``"w"`` would truncate and, on Windows, a truncating open of a
        held file raises rather than waiting). ``file_lock`` fails CLOSED — it
        raises rather than entering the critical section unserialised — so there
        is deliberately no lock-less fallback.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.touch(exist_ok=True)
        with open(self._lock_path, "r+") as handle:
            with file_lock(handle.fileno(), exclusive=True):
                yield

    def read(self) -> str:
        """The raw stored memory text, or ``""`` when nothing is stored yet.

        Any read failure degrades to ``""`` rather than raising: a first-run
        store is absent (``FileNotFoundError``), a torn parent is
        ``NotADirectoryError``, and an unreadable file (permissions, I/O error,
        the path being a directory) is ANY OTHER ``OSError`` — all of which the
        tier must treat as "no group memory this turn", never a turn-fatal crash.
        The design contract is that this tier is best-effort and self-defers to
        empty; catching only the first two would let a ``PermissionError`` on
        ``memory.md`` propagate out of the tier and fail every tagged session's
        turn.
        """
        try:
            return self._path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write(self, text: str) -> None:
        """Replace the group's shared memory with *text*, atomically and locked.

        Uses :func:`atomic_write` (unique ``mkstemp`` temp + rename) rather than a
        deterministic ``.tmp`` name — the latter is explicitly forbidden by the
        ``atomic_write`` module because concurrent writers targeting one fixed
        temp name collide (ENOENT / interleaved bytes). The per-group lock
        serialises writers so a replace and an append cannot interleave.
        """
        with self._locked():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self._path, text)

    def append(self, text: str, *, blob_max: int | None = None) -> str:
        """Append *text* as a new entry, under the lock; return the stored blob.

        The read-modify-write is held under the per-group lock for its whole
        span, so two concurrent coordinator appends serialise instead of the
        second silently overwriting the first (the lost-update the lock exists to
        prevent). Refuses with :class:`GroupMemoryBlobTooLarge` when the RESULT
        would exceed the total-blob cap — refused, not truncated, so a coordinator
        is told rather than the tail being silently dropped or the file growing
        unbounded. ``blob_max`` defaults to :data:`GROUP_MEMORY_BLOB_MAX` resolved
        at CALL time (not bound at def time) so the ceiling stays overridable.
        """
        cap = GROUP_MEMORY_BLOB_MAX if blob_max is None else blob_max
        with self._locked():
            existing = self.read().rstrip()
            new_text = (existing + _APPEND_SEPARATOR + text) if existing else text
            if cap > 0 and len(new_text) > cap:
                raise GroupMemoryBlobTooLarge(
                    f"appending would grow group memory to {len(new_text)} chars, "
                    f"past the {cap}-char cap; prune with mode='replace'"
                )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(self._path, new_text)
            return new_text

    def get_context(self, cap: int = GROUP_MEMORY_CAP, *, query: str = "") -> str:
        """A delimited block for prompt injection, or ``""`` when empty.

        ``query`` is accepted for signature parity with the other tiers'
        ``get_context`` (the blob is not query-ranked). ``cap`` bounds the
        injected length; overflow is truncated with a visible marker so the
        model is never handed a silently clipped document. A ``cap`` too small to
        hold even the marker yields ``""`` (a marker-only block is pure noise).
        """
        text = self.read().strip()
        if not text:
            return ""
        if cap > 0 and len(text) > cap:
            if cap <= len(_TRUNC_MARKER):
                return ""
            text = text[: cap - len(_TRUNC_MARKER)] + _TRUNC_MARKER
        return (
            "[PROJECT GROUP MEMORY — shared across every session in this project. "
            "Authored by you / the coordinator; treat as durable project context.]\n"
            f"{text}\n"
            "[END OF PROJECT GROUP MEMORY]"
        )


def delete_group_memory(group_id: str) -> bool:
    """Remove a group's entire shared-memory store. Returns whether it existed.

    Called by the group-memory GC when the project is deleted
    (:meth:`ProjectStore.delete_project`, design §12.6 GC). Best-effort and
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
