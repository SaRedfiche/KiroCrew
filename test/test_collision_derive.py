"""Tests for the collision-index git derivation (repo_id / repo_rel_path)."""

from __future__ import annotations

import os
import subprocess

import pytest

from kiro_crew.dashboard.collision_derive import (
    canonicalize_remote,
    derive_remote_target,
    derive_repo_context,
    repo_rel_for,
)


def _coords(abs_file, cwd):
    """Test helper: full derive via the split API (context + per-path relpath)."""
    ctx = derive_repo_context(cwd)
    if ctx is None:
        return None
    rel = repo_rel_for(ctx.repo_root, abs_file)
    if rel is None:
        return None
    return ctx.repo_id, rel, ctx.repo_root


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, cwd=str(cwd))


def _init_repo(path, *, remote=None):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    if remote:
        _git(path, "remote", "add", "origin", remote)
    return path


@pytest.fixture(autouse=True)
def _skip_without_git():
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")


class TestCanonicalizeRemote:
    def test_scp_and_https_forms_match(self):
        scp = canonicalize_remote("git@github.com:org/repo.git")
        https = canonicalize_remote("https://github.com/org/repo")
        assert scp == https == "github.com/org/repo"

    def test_strips_dotgit_and_lowercases_host_preserving_path_case(self):
        # Host is lowercased (DNS is case-insensitive); PATH case is preserved,
        # and .git + trailing slash are stripped.
        assert canonicalize_remote("https://Host/Org/Repo.git/") == "host/Org/Repo"

    def test_case_distinct_paths_do_not_merge(self):
        # On a case-sensitive host, org/Repo and org/repo are distinct repos;
        # folding them would emit a false collision (GPT-review Finding C).
        assert canonicalize_remote("git@host:org/Repo") != canonicalize_remote("git@host:org/repo")
        # But the HOST case is still folded, so scp/https forms match.
        assert canonicalize_remote("git@HOST:o/r") == canonicalize_remote("https://host/o/r")

    def test_empty(self):
        assert canonicalize_remote("") == ""
        assert canonicalize_remote(None) == ""


class TestDeriveRepoCoords:
    def test_basic_derive_with_remote(self, tmp_path):
        repo = _init_repo(tmp_path / "r", remote="git@github.com:org/repo.git")
        f = repo / "src" / "app.py"
        f.parent.mkdir(parents=True)
        f.write_text("x", encoding="utf-8")
        rc = _coords(str(f), str(repo))
        assert rc is not None
        repo_id, rel, root = rc
        assert repo_id == "github.com/org/repo"
        assert rel == os.path.join("src", "app.py")
        assert root == os.path.realpath(str(repo))

    def test_no_remote_falls_back_to_common_dir(self, tmp_path):
        repo = _init_repo(tmp_path / "r")  # no origin
        f = repo / "a.py"
        f.write_text("x", encoding="utf-8")
        rc = _coords(str(f), str(repo))
        assert rc is not None
        repo_id, rel, _ = rc
        assert repo_id == os.path.realpath(str(repo / ".git"))
        assert rel == "a.py"

    def test_out_of_tree_write_is_dropped(self, tmp_path):
        repo = _init_repo(tmp_path / "r", remote="git@h:o/r.git")
        outside = tmp_path / "outside.py"
        outside.write_text("x", encoding="utf-8")
        assert _coords(str(outside), str(repo)) is None

    def test_path_above_root_is_dropped(self, tmp_path):
        repo = _init_repo(tmp_path / "nested" / "r", remote="git@h:o/r.git")
        above = tmp_path / "nested" / "sibling.py"
        above.write_text("x", encoding="utf-8")
        assert _coords(str(above), str(repo)) is None

    def test_non_repo_cwd_returns_none(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert derive_repo_context(str(plain)) is None

    def test_git_failure_returns_none(self, tmp_path, monkeypatch):
        # A git subprocess that raises (not just nonzero) must be swallowed to
        # None, not propagate. Covers the _run_git except branch.
        import kiro_crew.dashboard.collision_derive as cd

        def _boom(*a, **k):
            raise OSError("git exploded")

        monkeypatch.setattr(cd.subprocess, "run", _boom)
        assert cd.derive_repo_context(str(tmp_path)) is None

    def test_two_worktrees_share_repo_id_differ_in_root(self, tmp_path):
        repo = _init_repo(tmp_path / "r", remote="git@github.com:org/repo.git")
        (repo / "a.py").write_text("x", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        wt = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", str(wt))
        rc_main = _coords(str(repo / "a.py"), str(repo))
        rc_wt = _coords(str(wt / "a.py"), str(wt))
        assert rc_main is not None and rc_wt is not None
        # repo_id matches (origin remote) -> the two worktrees collide on a.py;
        # worktree root differs.
        assert rc_main[0] == rc_wt[0] == "github.com/org/repo"
        assert rc_main[1] == rc_wt[1] == "a.py"
        assert rc_main[2] != rc_wt[2]

    def test_two_worktrees_share_common_dir_when_no_remote(self, tmp_path):
        repo = _init_repo(tmp_path / "r")  # no origin
        (repo / "a.py").write_text("x", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        wt = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", str(wt))
        rc_main = _coords(str(repo / "a.py"), str(repo))
        rc_wt = _coords(str(wt / "a.py"), str(wt))
        assert rc_main is not None and rc_wt is not None
        # No remote -> common dir; a worktree's common dir is the main repo's
        # .git, so the two still share repo_id (the multi-worktree match).
        assert rc_main[0] == rc_wt[0]


class TestNoSandboxBackendDegradesToNone:
    """On a host with no OS sandbox backend, the git probe must DROP (return
    None), never raise into the per-turn flush — the module's never-raise
    contract. Reproduces the Windows-CI SandboxUnavailableError path."""

    def test_derive_returns_none_when_sandbox_backend_unavailable(self, tmp_path, monkeypatch):
        import kiro_crew.dashboard.collision_derive as cd

        def _boom(*_a, **_k):
            raise cd.SandboxUnavailableError("no backend (test)", "no_backend", "probe: not Linux")

        monkeypatch.setattr(cd, "sandboxed_spawn_argv", _boom)
        # A real repo cwd, but the sandbox wrap refuses: derive must drop to None
        # rather than propagate SandboxUnavailableError.
        assert cd.derive_repo_context(str(tmp_path)) is None
        assert cd._run_git(str(tmp_path), "rev-parse", "--show-toplevel") is None


def _init_bare(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "--bare")
    return path


def _repo_tracking(path, upstream_bare, *, local_branch, remote_ref, remote_url=None):
    """A real repo whose ``local_branch`` tracks ``remote_ref``. A real push to a
    bare upstream establishes the tracking ref so ``@{u}`` resolves; when
    ``remote_url`` is given (URL-shape tests) the origin url is REWRITTEN to it
    afterward, so the derived target reflects that spelling while ``@{u}`` stays
    valid."""
    bare = (
        upstream_bare
        if upstream_bare is not None
        else _init_bare(path.parent / f"{path.name}-up.git")
    )
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "remote", "add", "origin", str(bare))
    (path / "f.py").write_text("x", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    if local_branch != "master":
        _git(path, "branch", "-m", local_branch)
    _git(path, "push", "-q", "-u", "origin", f"{local_branch}:{remote_ref}")
    if remote_url is not None:
        # Rewrite the fetch url to the spelling under test; the tracking ref
        # created by the push above keeps @{u} resolvable.
        _git(path, "config", "remote.origin.url", remote_url)
    return path


class TestDeriveRemoteTarget:
    def test_upstream_resolves_to_canonical_target(self, tmp_path):
        bare = _init_bare(tmp_path / "up.git")
        repo = _repo_tracking(
            tmp_path / "r", bare, local_branch="feature/x", remote_ref="feature/x"
        )
        target = derive_remote_target(str(repo))
        # The remote URL is a local path (the bare repo) -> canonicalized as-is
        # with no host; the ref is appended after '#'.
        assert target.endswith("#feature/x")
        assert target == f"{canonicalize_remote(str(bare))}#feature/x"

    def test_local_branch_name_does_not_appear__only_upstream_ref(self, tmp_path):
        # Two sessions on DIFFERENTLY-named local branches tracking the SAME
        # remote ref must produce the SAME target (the hazard is the shared
        # push destination, not the local name). Each clone has its own bare
        # upstream (so the two independent-history pushes do not conflict), with
        # both origin urls rewritten to one shared canonical url.
        shared = "git@github.com:org/repo.git"
        a = _repo_tracking(
            tmp_path / "a", None, local_branch="alice/work", remote_ref="main", remote_url=shared
        )
        b = _repo_tracking(
            tmp_path / "b", None, local_branch="bob/work", remote_ref="main", remote_url=shared
        )
        ta, tb = derive_remote_target(str(a)), derive_remote_target(str(b))
        assert ta and ta == tb
        assert ta.endswith("#main")
        assert ta == "github.com/org/repo#main"

    def test_distinct_upstream_refs_differ(self, tmp_path):
        a = _repo_tracking(
            tmp_path / "a",
            None,
            local_branch="x",
            remote_ref="feature/a",
            remote_url="git@github.com:org/repo.git",
        )
        b = _repo_tracking(
            tmp_path / "b",
            None,
            local_branch="y",
            remote_ref="feature/b",
            remote_url="git@github.com:org/repo.git",
        )
        assert derive_remote_target(str(a)) != derive_remote_target(str(b))

    def test_no_upstream_returns_empty(self, tmp_path):
        # A committed branch with NO tracking configured contributes no target.
        repo = _init_repo(tmp_path / "r", remote="git@github.com:org/repo.git")
        (repo / "f.py").write_text("x", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")
        assert derive_remote_target(str(repo)) == ""

    def test_non_repo_cwd_returns_empty(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert derive_remote_target(str(plain)) == ""

    def test_scp_and_https_upstream_remotes_produce_same_target(self, tmp_path):
        # scp-form and https-form of one remote canonicalize equal, so two
        # sessions configured with different URL spellings of the same remote
        # still collide. Uses the URL-shape path (no reachable push).
        a = _repo_tracking(
            tmp_path / "a",
            None,
            local_branch="x",
            remote_ref="main",
            remote_url="git@github.com:org/repo.git",
        )
        b = _repo_tracking(
            tmp_path / "b",
            None,
            local_branch="y",
            remote_ref="main",
            remote_url="https://github.com/org/repo",
        )
        ta, tb = derive_remote_target(str(a)), derive_remote_target(str(b))
        assert ta == tb == "github.com/org/repo#main"

    def test_pushurl_wins_over_fetch_url(self, tmp_path):
        # When remote.origin.pushurl is set, the push target uses it (the real
        # destination), not the fetch url.
        repo = _repo_tracking(
            tmp_path / "a",
            None,
            local_branch="x",
            remote_ref="main",
            remote_url="https://github.com/org/fetch-mirror",
        )
        _git(repo, "config", "remote.origin.pushurl", "git@github.com:org/real-push.git")
        assert derive_remote_target(str(repo)) == "github.com/org/real-push#main"

    def test_git_failure_returns_empty(self, tmp_path, monkeypatch):
        import kiro_crew.dashboard.collision_derive as cd

        def _boom(*a, **k):
            raise OSError("git exploded")

        monkeypatch.setattr(cd.subprocess, "run", _boom)
        assert cd.derive_remote_target(str(tmp_path)) == ""

    def test_context_carries_remote_target(self, tmp_path):
        # derive_repo_context must populate remote_target in the same off-loop
        # pass (the flush reuses it).
        bare = _init_bare(tmp_path / "up.git")
        repo = _repo_tracking(
            tmp_path / "r", bare, local_branch="feature/x", remote_ref="feature/x"
        )
        ctx = derive_repo_context(str(repo))
        assert ctx is not None
        assert ctx.remote_target.endswith("#feature/x")
