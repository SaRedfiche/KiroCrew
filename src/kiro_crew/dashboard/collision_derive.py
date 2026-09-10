"""Off-loop git derivation for the same-file collision index (Signal 1).

Turns a written file's absolute path + the session's cwd into the collision
key components the index needs: ``repo_id`` (stable across worktrees of one
repo) and ``repo_rel_path`` (repo-relative, so two worktrees match). This runs
in a worker thread from the per-turn flush — NEVER on the event loop — because
every step stats the filesystem or spawns ``git``.

Design (peer-coordination §4.2, Signal 1):

* ``repo_id`` = the ``origin`` remote URL when present (canonicalized), else the
  repo's ``git rev-parse --git-common-dir`` — NOT ``worktree_root``. Two
  worktrees of one repo share an origin and a common dir but have different
  toplevels, so keying on the toplevel would defeat the multi-worktree match.
* ``repo_rel_path`` = ``relpath(realpath(file), realpath(repo_root))``, both
  realpath'd so a symlinked worktree root (macOS ``/tmp``→``/private/tmp``) still
  compares. If the file is NOT under the repo root (an out-of-tree write —
  ``/tmp``, a sibling repo, a path above root), it is DROPPED (returns ``None``):
  an out-of-repo write has no well-defined project-relative key and must not
  manufacture a ``../..``-shaped one.
* A ``git`` failure or a non-repo path returns ``None`` (dropped), never raises
  into the caller — a derive miss just means no collision entry for that write.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

_GIT_TIMEOUT_SECS = 5.0


def _run_git(cwd: str, *args: str) -> str | None:
    """Run ``git -C cwd <args>`` and return stripped stdout, or None on any
    failure/timeout. Read-only rev-parse/config calls only."""
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def canonicalize_remote(url: str) -> str:
    """Normalize a git remote URL so scp-form and https-form of one repo match.

    Strips a trailing ``.git`` and ``/``, lowercases, and rewrites
    ``git@host:org/repo`` scp form to ``host/org/repo`` so it compares equal to
    ``https://host/org/repo``. Best-effort: an unrecognized shape is just
    lowercased+stripped, which still matches itself across worktrees.
    """
    u = (url or "").strip()
    if not u:
        return ""
    # scp-like: git@host:org/repo(.git)
    if "://" not in u and "@" in u and ":" in u:
        _, _, rest = u.partition("@")
        host, _, path = rest.partition(":")
        u = f"{host}/{path}"
    else:
        # strip scheme
        _, sep, rest = u.partition("://")
        if sep:
            # drop any userinfo@ in the authority
            u = rest.split("@", 1)[-1]
    u = u.lower().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return u.rstrip("/")


@dataclass(frozen=True)
class RepoContext:
    """The cwd-level git identity, derived ONCE per session cwd per flush.

    ``repo_id`` is stable across worktrees of one repo; ``repo_root`` is the
    realpath'd toplevel used only to compute repo-relative paths. Deriving this
    once and reusing it for every path written this turn collapses the per-path
    git spawns from ~3N to ~2 (Security-review Medium: executor starvation).
    """

    repo_id: str
    repo_root: str


def derive_repo_context(cwd: str) -> RepoContext | None:
    """Derive the cwd-level (repo_id, repo_root) with the git calls run ONCE.

    Returns None for a non-repo cwd or a git failure. Blocking — call via
    ``asyncio.to_thread``. repo_id = canonicalized origin remote else realpath'd
    ``--git-common-dir`` (stable across worktrees; NOT the toplevel).
    """
    if not cwd:
        return None
    toplevel = _run_git(cwd, "rev-parse", "--show-toplevel")
    if not toplevel:
        return None
    try:
        repo_root = os.path.realpath(toplevel)
    except OSError:
        return None
    repo_id = ""
    remote = _run_git(cwd, "config", "--get", "remote.origin.url")
    if remote:
        repo_id = canonicalize_remote(remote)
    if not repo_id:
        common = _run_git(cwd, "rev-parse", "--git-common-dir")
        if common:
            repo_id = os.path.realpath(
                common if os.path.isabs(common) else os.path.join(cwd, common)
            )
    if not repo_id:
        return None
    return RepoContext(repo_id=repo_id, repo_root=repo_root)


def repo_rel_for(repo_root: str, abs_file_path: str) -> str | None:
    """Repo-relative path for a written file, or None if out-of-tree.

    Pure filesystem math (one ``realpath``), NO git — so the per-path work in a
    flush is cheap once ``derive_repo_context`` has run. An out-of-tree write
    (``/tmp``, a sibling repo, a path above root) returns None: it has no
    well-defined project-relative key and must not manufacture a ``..``-shaped
    one.
    """
    if not repo_root or not abs_file_path:
        return None
    try:
        real_file = os.path.realpath(abs_file_path)
    except OSError:
        return None
    try:
        if os.path.commonpath([repo_root, real_file]) != repo_root:
            return None
    except ValueError:
        return None
    rel = os.path.relpath(real_file, repo_root)
    if rel == "." or rel.startswith(".."):
        return None
    return rel

