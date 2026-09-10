"""Tests for the projects.json store (ProjectStore) — Phase 1, Step 2.

Covers the durability + contract properties the design and gate require:
idempotent-by-id create, concurrent-create dedup (TOCTOU), delete-always-
proceeds, repos[] auto-derive (append-only, deduped), fail-loud on a corrupt
(present-but-unparseable) file, and transactional-save rollback on a write
failure. Uses a tmp_path base_dir like the cron-store tests.
"""

from __future__ import annotations

import json
import threading

import pytest

from kiro_crew.dashboard.project_store import (
    Project,
    ProjectStore,
    ProjectStoreBusy,
    ProjectStoreCorrupt,
)


class TestCreate:
    def test_create_persists(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        rec = store.create_project("My Project", project_id="grp-mp")
        assert rec.id == "grp-mp" and rec.name == "My Project" and rec.repos == []
        # Reload from disk in a fresh store instance: it round-trips.
        assert ProjectStore(base_dir=tmp_path).get_project("grp-mp").name == "My Project"

    def test_create_is_idempotent_by_id(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        first = store.create_project("Foo", project_id="grp-fixed")
        again = store.create_project("Different Name", project_id="grp-fixed")
        assert again.id == first.id == "grp-fixed"
        assert again.name == "Foo"  # existing record returned, not overwritten
        assert len(store.list_projects()) == 1

    def test_names_are_not_unique(self, tmp_path):
        """Same name, distinct ids -> two DISTINCT projects (names aren't keys)."""
        store = ProjectStore(base_dir=tmp_path)
        a = store.create_project("Foo", project_id="grp-a")
        b = store.create_project("Foo", project_id="grp-b")
        assert a.id != b.id
        assert len(store.list_projects()) == 2

    def test_empty_name_rejected(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        with pytest.raises(ValueError):
            store.create_project("   ", project_id="grp-x")

    def test_missing_id_rejected(self, tmp_path):
        """project_id is required (caller mints it) — no silent fallback mode."""
        store = ProjectStore(base_dir=tmp_path)
        with pytest.raises(ValueError):
            store.create_project("Named", project_id="")

    def test_project_cap_raises(self, tmp_path, monkeypatch):
        """The _MAX_PROJECTS ceiling raises ProjectStoreBusy for a new id once
        full — but an existing id still short-circuits idempotently before the
        cap check, so a re-create of a stored project never spuriously fails."""
        import kiro_crew.dashboard.project_store as ps

        monkeypatch.setattr(ps, "_MAX_PROJECTS", 2)
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("A", project_id="grp-a")
        store.create_project("B", project_id="grp-b")
        with pytest.raises(ProjectStoreBusy):
            store.create_project("C", project_id="grp-c")
        # Idempotent re-create of an existing id is still fine at the cap.
        assert store.create_project("A", project_id="grp-a").id == "grp-a"


class TestConcurrentCreate:
    def test_racing_same_id_creates_one(self, tmp_path):
        """Two threads creating the same id under the lock -> exactly one record
        (the _sync_for_write + in-lock existence check closes the TOCTOU)."""
        store = ProjectStore(base_dir=tmp_path)
        errors: list[Exception] = []

        def _create():
            try:
                store.create_project("Race", project_id="grp-race")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=_create) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        # One record on disk, regardless of thread interleaving.
        reloaded = ProjectStore(base_dir=tmp_path)
        assert len([p for p in reloaded.list_projects() if p.id == "grp-race"]) == 1

    def test_cross_instance_same_id_dedups_via_sync(self, tmp_path):
        """Drives the actual TOCTOU window (not thread luck): instance A loads
        empty, instance B creates grp-x and commits to disk, THEN A creates
        grp-x. A's _sync_for_write must reload B's record and the in-lock
        existence check must return it — so the store holds exactly one grp-x,
        not two. This fails if _sync_for_write (or the post-sync existence
        check) is removed, unlike the thread-scheduled test above."""
        a = ProjectStore(base_dir=tmp_path)  # loads empty
        b = ProjectStore(base_dir=tmp_path)  # separate cache
        b.create_project("FromB", project_id="grp-x")  # B commits to disk
        # A still has an empty cache; its create must sync-in B's record first.
        rec = a.create_project("FromA", project_id="grp-x")
        assert rec.name == "FromB"  # idempotent: B's record returned, not A's
        assert [p.id for p in ProjectStore(base_dir=tmp_path).list_projects()] == ["grp-x"]


class TestReposAutoDerive:
    def test_observe_repo_appends_deduped(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        rec = store.create_project("P", project_id="grp-r")
        assert store.observe_repo("grp-r", "git@host:org/repo-a.git") is True
        assert store.observe_repo("grp-r", "git@host:org/repo-b.git") is True
        # Duplicate is a no-op, order preserved.
        assert store.observe_repo("grp-r", "git@host:org/repo-a.git") is False
        repos = store.get_project("grp-r").repos
        assert repos == ["git@host:org/repo-a.git", "git@host:org/repo-b.git"]

    def test_observe_repo_absent_project_is_noop(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        assert store.observe_repo("nonexistent", "git@host:x.git") is False

    def test_observe_repo_empty_id_is_noop(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-r")
        assert store.observe_repo("grp-r", "   ") is False
        assert store.get_project("grp-r").repos == []

    def test_repos_survive_reload(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-r2")
        store.observe_repo("grp-r2", "remote-1")
        assert ProjectStore(base_dir=tmp_path).get_project("grp-r2").repos == ["remote-1"]


class TestDelete:
    def test_delete_always_proceeds(self, tmp_path):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-del")
        assert store.delete_project("grp-del") is True
        assert store.get_project("grp-del") is None
        # Idempotent: deleting again is a benign False, not an error.
        assert store.delete_project("grp-del") is False

    def test_delete_does_not_consult_sessions(self, tmp_path):
        """No refuse-if-nonempty path exists — delete removes the record even
        if (hypothetically) a session still points at it (dangling id is a
        reader concern, not a delete guard)."""
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-live")
        # No session wiring passed in; delete must not require any.
        assert store.delete_project("grp-live") is True


class TestDurability:
    def test_corrupt_file_raises_not_empties(self, tmp_path):
        """A present, non-empty file whose bytes do NOT parse must fail loud
        (ProjectStoreCorrupt), NOT degrade to empty — the file is machine-owned,
        so unparseable bytes are corruption, and degrading would let the next
        write overwrite whatever was really there. (Absent/empty is empty.)"""
        (tmp_path / "projects.json").write_text("{ this is not valid json", encoding="utf-8")
        with pytest.raises(ProjectStoreCorrupt):
            ProjectStore(base_dir=tmp_path)

    def test_absent_file_is_empty(self, tmp_path):
        # No file at all -> a genuinely empty store, no raise.
        store = ProjectStore(base_dir=tmp_path)
        assert store.list_projects() == []
        rec = store.create_project("First", project_id="grp-1")
        assert ProjectStore(base_dir=tmp_path).get_project(rec.id) is not None

    def test_non_list_projects_raises(self, tmp_path):
        """A machine-owned file we never write this way is corruption, not a
        row to salvage: a present-but-non-list `projects` (or a non-object top
        level) fails loud."""
        for bad in ('{"projects": null}', '{"projects": 5}', '{"projects": "x"}', "[]", "5"):
            (tmp_path / "projects.json").write_text(bad, encoding="utf-8")
            with pytest.raises(ProjectStoreCorrupt):
                ProjectStore(base_dir=tmp_path)

    def test_empty_object_is_empty_store(self, tmp_path):
        # {} has no "projects" key at all -> not a list -> corrupt. But an empty
        # projects list is a legitimate empty store.
        (tmp_path / "projects.json").write_text('{"projects": []}', encoding="utf-8")
        assert ProjectStore(base_dir=tmp_path).list_projects() == []

    def test_unreadable_present_store_raises_not_empties(self, tmp_path, monkeypatch):
        """A present-but-UNREADABLE store must re-raise, NOT degrade to empty —
        else the next write would overwrite a store we merely could not read."""
        import kiro_crew.dashboard.project_store as ps

        (tmp_path / "projects.json").write_text(
            '{"projects":[{"id":"keep","name":"Keep","repos":[]}]}', encoding="utf-8"
        )
        real_read_bytes = ps.Path.read_bytes

        def _boom(self):
            if self.name == "projects.json":
                raise PermissionError("simulated unreadable store")
            return real_read_bytes(self)

        monkeypatch.setattr(ps.Path, "read_bytes", _boom)
        with pytest.raises(OSError):
            ProjectStore(base_dir=tmp_path)  # construction fails loudly, no data loss

    def test_malformed_record_raises(self, tmp_path):
        """We author every record via json.dumps, so a record missing a required
        field (or with a wrong-typed one) is corruption — fail loud, do not
        skip or coerce. A validated creation interface is the only writer."""
        bad_records = [
            {"name": "no-id"},                       # missing id
            {"id": "x"},                             # missing name (and repos)
            {"id": 5, "name": "n", "repos": []},     # non-str id
            {"id": "x", "name": "n", "repos": 5},    # non-list repos
            {"id": "x", "name": "n", "repos": [3]},  # non-str repo entry
        ]
        for rec in bad_records:
            (tmp_path / "projects.json").write_text(
                json.dumps({"projects": [rec]}), encoding="utf-8"
            )
            with pytest.raises(ProjectStoreCorrupt):
                ProjectStore(base_dir=tmp_path)

    def test_external_corruption_reraises_not_overwrites(self, tmp_path):
        """A store loaded clean, then corrupted externally: the next mutation
        must RE-RAISE (its _sync_for_write reloads and _load raises), NOT
        overwrite the corrupt file from stale cached records. Guards the
        _last_digest ordering — a failed load must not poison the digest."""
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("First", project_id="grp-1")
        # Another process (or a disk fault) corrupts the file.
        (tmp_path / "projects.json").write_text("{ corrupt !!", encoding="utf-8")
        with pytest.raises(ProjectStoreCorrupt):
            store.create_project("Second", project_id="grp-2")
        # The corrupt bytes are still on disk — NOT overwritten from stale cache.
        assert (tmp_path / "projects.json").read_text(encoding="utf-8") == "{ corrupt !!"

    def test_external_write_picked_up_before_rmw(self, tmp_path):
        """_sync_for_write reloads if the file changed under us, so a create
        does not clobber a concurrent write by another of our processes."""
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("First", project_id="grp-1")
        # Simulate another of our processes writing directly (well-formed).
        (tmp_path / "projects.json").write_text(
            json.dumps(
                {
                    "projects": [
                        {"id": "grp-1", "name": "First", "repos": []},
                        {"id": "grp-2", "name": "External", "repos": []},
                    ]
                }
            ),
            encoding="utf-8",
        )
        # Our next create must not drop grp-2.
        store.create_project("Third", project_id="grp-3")
        ids = {p.id for p in ProjectStore(base_dir=tmp_path).list_projects()}
        assert ids == {"grp-1", "grp-2", "grp-3"}


class TestSaveRollback:
    """A failed _save must leave NO uncommitted mutation in the in-memory cache
    (else a retry sees it as already-applied and never persists it). After a
    failed save the cache must equal the committed on-disk state, and a retry
    must then persist cleanly."""

    def _break_atomic_write(self, monkeypatch):
        import kiro_crew.dashboard.project_store as ps

        def _boom(path, payload):
            raise OSError("simulated disk-full during atomic_write")

        monkeypatch.setattr(ps, "atomic_write", _boom)

    def test_create_rolls_back_on_save_failure(self, tmp_path, monkeypatch):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("Existing", project_id="grp-1")
        self._break_atomic_write(monkeypatch)
        with pytest.raises(OSError):
            store.create_project("New", project_id="grp-2")
        # Cache must NOT reflect the failed create.
        assert {p.id for p in store.list_projects()} == {"grp-1"}
        # Un-break: a retry must now persist, not be a false idempotent no-op.
        monkeypatch.undo()
        store.create_project("New", project_id="grp-2")
        assert {p.id for p in ProjectStore(base_dir=tmp_path).list_projects()} == {"grp-1", "grp-2"}

    def test_observe_repo_rolls_back_nested_on_save_failure(self, tmp_path, monkeypatch):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-1")
        store.observe_repo("grp-1", "repo-a")
        self._break_atomic_write(monkeypatch)
        with pytest.raises(OSError):
            store.observe_repo("grp-1", "repo-b")
        # The nested rec.repos.append must be undone too, not just the list.
        assert store.get_project("grp-1").repos == ["repo-a"]
        monkeypatch.undo()
        store.observe_repo("grp-1", "repo-b")
        reloaded = ProjectStore(base_dir=tmp_path).get_project("grp-1")
        assert reloaded.repos == ["repo-a", "repo-b"]

    def test_delete_rolls_back_on_save_failure(self, tmp_path, monkeypatch):
        store = ProjectStore(base_dir=tmp_path)
        store.create_project("P", project_id="grp-1")
        self._break_atomic_write(monkeypatch)
        with pytest.raises(OSError):
            store.delete_project("grp-1")
        assert {p.id for p in store.list_projects()} == {"grp-1"}  # still present
        monkeypatch.undo()
        assert store.delete_project("grp-1") is True
        assert ProjectStore(base_dir=tmp_path).list_projects() == []


class TestProjectDataclass:
    def test_from_dict_round_trips_what_we_wrote(self):
        rec = Project(id="x", name="N", repos=["a", "b"])
        assert Project.from_dict(rec.to_dict()) == rec

    def test_from_dict_rejects_unknown_keys(self):
        # Exact-shape contract: our writer emits only {id,name,repos}. An extra
        # key means the record is corrupt or foreign, not a version we produced.
        with pytest.raises(ProjectStoreCorrupt):
            Project.from_dict({"id": "x", "name": "N", "repos": [], "extra": 1})

    def test_from_dict_rejects_malformed(self):
        # Machine-owned schema: a record that isn't the shape we write is
        # corruption, not input to coerce.
        for bad in ({"name": "no-id"}, {"id": 5, "name": "n", "repos": []},
                    {"id": "x", "name": "n", "repos": "nope"},
                    {"id": "", "name": "n", "repos": []}):
            with pytest.raises(ProjectStoreCorrupt):
                Project.from_dict(bad)
