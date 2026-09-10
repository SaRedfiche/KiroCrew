"""Project-coordination store — the ``projects.json`` record table (Phase 1).

A *project* groups sibling chat sessions that work the same project. A session
carries an opaque ``project_group_id`` (see ``_ChatSlot.project_group_id``);
this store holds the record each id points at: ``{id, name, repos[]}``.

Deliberately small. It mirrors the ``crons.json`` durability pattern — a
cross-process advisory ``flock``, a content-digest sync that detects an external
write before a read-modify-write, and an atomic tmp→rename save via
:func:`~kiro_crew.atomic_write.atomic_write` — but carries none of the cron
service's scheduler/timer/event-loop machinery, because a project record has no
runtime behaviour of its own.

The file is **machine-owned**: the only writer is this class, which always
serializes via ``json.dumps`` and writes atomically. We do not accept
hand-authored records, so the store validates its OWN invariants (a failed
write must not lose data; a concurrent process must not be clobbered) but does
NOT try to salvage foreign input — a file that does not match the shape we wrote
is corruption and fails loud (``ProjectStoreCorrupt``). A validated creation
interface (the tagging API, Step 4) is the only intended caller.

Design decisions (from the peer-coordination design, §4.1/§4.6/§9):

* **Create is idempotent by id**, not by name. Names are NOT unique — two
  projects may share a display name — so a by-name dedup would wrongly collapse
  distinct projects. The caller mints the id; a second create of the same id is
  a no-op that returns the existing record.
* **Delete always proceeds.** It never refuses on "a session still points here":
  the session store and this store share no lock, so that emptiness check is an
  unclosable TOCTOU (the ``chat_folders`` delete path reached the same
  conclusion). A dangling ``project_group_id`` is rendered under an
  "unknown project" bucket by readers, never a crash.
* **``repos[]`` is an auto-derived rollup**, append-only with manual prune — not
  a list the user must curate (that would re-import the filing-discipline burden
  a prior design was rejected for). :meth:`observe_repo` adds a repo id a tagged
  session's worktree yielded, deduped, order-preserving.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from kiro_crew import platform_compat
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.paths import config_dir

logger = logging.getLogger(__name__)

_PROJECTS_FILE = "projects.json"
_LOCK_FILE = ".projects.lock"

# Bounded, non-blocking lock spin — same rationale as the cron store: a blocking
# flock on the gateway loop would freeze every session while another process
# (the CLI, a concurrent handler) held it. We poll instead and raise.
_LOCK_TIMEOUT_SECS = 5.0
_LOCK_POLL_SECS = 0.05

_MAX_NAME_CHARS = 200
_MAX_PROJECTS = 500  # ceiling, mirroring MAX_CHAT_FOLDERS; only a runaway hits it


class ProjectStoreBusy(TimeoutError):
    """The store lock stayed contended past the timeout — retryable."""


class ProjectStoreCorrupt(ValueError):
    """``projects.json`` is present but does not match the shape we wrote.

    This store is machine-owned: the ONLY writer is ``ProjectStore``, which
    always serializes via ``json.dumps`` and writes atomically. We do not accept
    hand-authored records, so a file that fails to parse — or a record missing a
    required field — is corruption or a bug, not user input to salvage. We fail
    loud (raise) rather than coerce or degrade to empty, because degrading would
    let the next write silently overwrite whatever was really there. An absent
    or empty file is not corrupt — it is a fresh, empty store.
    """


@dataclass
class Project:
    """One project-coordination record."""

    id: str
    name: str
    repos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "repos": list(self.repos),
        }

    _FIELDS = ("id", "name", "repos")

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        """Rehydrate a record WE wrote, validating its exact shape.

        The file is machine-owned (see ``ProjectStoreCorrupt``): our writer emits
        exactly ``{id: str, name: str (non-empty id), repos: list[str]}`` and
        nothing else. Anything off that shape — a missing field, a wrong type, OR
        an unexpected key — is corruption or a bug, not input to coerce, so we
        raise ``ProjectStoreCorrupt``. We deliberately do NOT tolerate unknown
        keys: there is no schema-evolution writer that produces them, so
        accepting them would only mask a corrupt or foreign record.
        """
        if (
            not isinstance(d, dict)
            or set(d) != set(cls._FIELDS)
            or not isinstance(d["id"], str)
            or not d["id"]
            or not isinstance(d["name"], str)
            or not isinstance(d["repos"], list)
            or not all(isinstance(r, str) for r in d["repos"])
        ):
            raise ProjectStoreCorrupt(f"malformed project record: {d!r}")
        return cls(id=d["id"], name=d["name"], repos=list(d["repos"]))


class ProjectStore:
    """Durable ``{id, name, repos[]}`` records with flock + atomic write."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = Path(base_dir) if base_dir is not None else config_dir()
        self._path = self._dir / _PROJECTS_FILE
        self._projects: list[Project] = []
        self._last_digest: bytes = b""
        self._load()

    # ── disk core ──────────────────────────────────────────────────────────

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Cross-process advisory lock, bounded non-blocking spin (cron pattern)."""
        self._dir.mkdir(parents=True, exist_ok=True)
        lock_path = self._dir / _LOCK_FILE
        fd = lock_path.open("w")
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECS
        try:
            while not platform_compat.try_acquire_lock(fd.fileno(), exclusive=True):
                if time.monotonic() >= deadline:
                    raise ProjectStoreBusy(
                        f"Could not acquire project store lock within {_LOCK_TIMEOUT_SECS:g}s"
                    )
                time.sleep(_LOCK_POLL_SECS)
            try:
                yield
            finally:
                platform_compat.release_lock(fd.fileno())
        finally:
            fd.close()

    def _read_bytes(self) -> bytes:
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return b""  # genuinely absent -> a fresh, empty store
        except OSError:
            # Present but UNREADABLE (permissions, I/O error). NOT the same as
            # empty: treating it as empty would let the next write overwrite a
            # store we simply could not read. Fail loud; a caller may retry once
            # the fault clears.
            logger.warning("project store read failed: %s", self._path, exc_info=True)
            raise

    def _load(self) -> None:
        """Parse the store into memory.

        The file is machine-owned (see ``ProjectStoreCorrupt``). An absent/empty
        file is a fresh, empty store. Anything else must match the shape we
        wrote — valid JSON, a top-level object with a ``projects`` list of
        well-formed records — or we raise ``ProjectStoreCorrupt`` rather than
        salvage foreign input we never produce.
        """
        raw = self._read_bytes()
        if not raw:
            self._projects = []
            self._last_digest = hashlib.blake2b(raw).digest()
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            logger.warning("project store is not valid JSON: %s", self._path)
            raise ProjectStoreCorrupt(f"{self._path} is not valid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
            raise ProjectStoreCorrupt(f"{self._path} is not a projects object")
        # from_dict raises ProjectStoreCorrupt on any record that isn't the shape
        # we wrote — a malformed record is a bug/corruption, not a row to skip.
        parsed = [Project.from_dict(rec) for rec in data["projects"]]
        # Record the digest ONLY after a fully successful load. If any step above
        # raised, _last_digest keeps its prior value, so a later _sync_for_write
        # still sees the corrupt file as "changed", reloads, and re-raises —
        # rather than treating the poisoned digest as up-to-date and overwriting
        # the corrupt file from stale cache.
        self._projects = parsed
        self._last_digest = hashlib.blake2b(raw).digest()

    def _sync_for_write(self) -> None:
        """Reload if the file changed underneath us, so a RMW sees fresh state.

        Called inside the lock before any mutation: closes the
        snapshot-then-write TOCTOU exactly as the cron store's ``_sync_for_write``
        does — a concurrent process's write is picked up before we append.
        """
        current = hashlib.blake2b(self._read_bytes()).digest()
        if current != self._last_digest:
            self._load()

    def _save(self, rollback: list[Project]) -> None:
        """Atomic tmp→rename write; refresh the digest to our just-written state.

        Transactional: a mutator mutates ``self._projects`` in place, then calls
        this with a snapshot of the pre-mutation records. If ``atomic_write``
        fails, the in-memory cache is restored to ``rollback`` before the error
        propagates, so a failed save leaves no uncommitted mutation cached (a
        retry would otherwise see it as already-applied and never persist it).

        ``rollback`` restores only the top-level list. A nested-field mutation
        (``observe_repo``'s ``rec.repos.append``) is on a shared ``Project`` the
        snapshot still holds by reference, so its caller reverts that nested
        state in its own ``except`` — ``observe_repo`` does.
        """
        payload = json.dumps(
            {"projects": [p.to_dict() for p in self._projects]},
            separators=(",", ":"),
        )
        try:
            atomic_write(self._path, payload)
        except Exception:
            self._projects = rollback
            raise
        self._last_digest = hashlib.blake2b(payload.encode("utf-8")).digest()

    # ── reads (cache-only; cheap, no lock) ───────────────────────────────────
    #
    # Known invariant (Design-review Watch, SHA 4b46eddf2): reads serve the
    # in-process cache with NO lock and NO _sync_for_write, so a long-lived
    # store instance may serve STALE data after another process mutates the
    # file. This matches the crons.json read model. Only the write path picks
    # up an external change (via _sync_for_write). A future read-then-act path
    # that needs freshness must re-read under the lock itself, not rely on these.

    def list_projects(self) -> list[Project]:
        return list(self._projects)

    def get_project(self, project_id: str) -> Project | None:
        """Look up a record by id from the in-process cache.

        Cache-only: may serve STALE data if another process mutated the file
        since load (see the read-section invariant above). A caller that must
        act on freshness re-reads under the lock; this does not.
        """
        if not project_id:
            return None
        return next((p for p in self._projects if p.id == project_id), None)

    # ── mutations (locked read-modify-write) ─────────────────────────────────

    def create_project(self, name: str, project_id: str) -> Project:
        """Create a project (idempotent by id). Returns the record.

        ``project_id`` is REQUIRED and minted by the caller — a supplied id that
        already exists is a no-op returning the existing record (the
        idempotent-by-id contract). Names are not unique, so two distinct ids
        with the same name are two distinct projects.
        """
        clean_name = (name or "").strip()[:_MAX_NAME_CHARS]
        if not clean_name:
            raise ValueError("project name is required")
        pid = (project_id or "").strip()
        if not pid:
            raise ValueError("project_id is required")
        with self._file_lock():
            self._sync_for_write()
            existing = next((p for p in self._projects if p.id == pid), None)
            if existing is not None:
                return existing  # idempotent by id
            if len(self._projects) >= _MAX_PROJECTS:
                raise ProjectStoreBusy(f"project cap reached ({_MAX_PROJECTS})")
            record = Project(id=pid, name=clean_name)
            snapshot = list(self._projects)
            self._projects.append(record)
            self._save(rollback=snapshot)
            return record

    def observe_repo(self, project_id: str, repo_id: str) -> bool:
        """Append a repo id to a project's derived ``repos[]`` rollup.

        Append-only + deduped + order-preserving. No-op (returns False) when the
        project is absent or the repo is already listed. This is how ``repos[]``
        auto-populates as tagged sessions join — never a user-curated list.
        """
        repo = (repo_id or "").strip()
        if not repo:
            return False
        with self._file_lock():
            self._sync_for_write()
            rec = next((p for p in self._projects if p.id == project_id), None)
            if rec is None or repo in rec.repos:
                return False
            repos_snapshot = list(rec.repos)  # nested-list rollback (see _save)
            rec.repos.append(repo)
            try:
                self._save(rollback=list(self._projects))
            except Exception:
                # _save restored the _projects pointer, but the nested
                # rec.repos.append is on the shared Project object — undo it too
                # so a failed save leaves NO uncommitted mutation cached.
                rec.repos[:] = repos_snapshot
                raise
            return True

    def delete_project(self, project_id: str) -> bool:
        """Delete a project. ALWAYS proceeds — never refuses on live members.

        Returns whether a record was removed. A session whose
        ``project_group_id`` still points here is not consulted (cross-store
        TOCTOU, §4.1): its tag becomes dangling and readers bucket it under
        "unknown project".
        """
        with self._file_lock():
            self._sync_for_write()
            before = len(self._projects)
            snapshot = list(self._projects)
            self._projects = [p for p in self._projects if p.id != project_id]
            if len(self._projects) == before:
                return False
            self._save(rollback=snapshot)
            return True
