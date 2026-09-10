"""Project panel read endpoint — the browse view for project coordination (§4.4).

``GET /api/projects/{id}/panel`` returns a per-project snapshot the dashboard
renders: the project record (name, repos), every LIVE session tagged with the
project (title, agent, current branch), and the collision flags (same-file +
same-worktree). Read-only; the frontend view is a later, separate piece — this
is the backend JSON it consumes.

Flag availability is EVENTUAL, not instantaneous: a session contributes to the
same-file index and to the same-worktree map only once it has taken a turn (the
per-turn flush is what records its edits and derives its worktree_root). So two
just-opened sessions in one repo do not show a same-worktree flag until each has
flushed at least once. This is the accepted Phase-1 approximation of the design's
"standing" view (eager re-derive on every slot.project commit is a later
hardening); the panel reflects what the indices actually hold.

Mirrors ``handlers/files.py::api_project_git`` for the state + off-loop +
SEL-audit + json_response shape, and reuses ``_project_git_branch`` for a cheap
no-subprocess branch read. Session enumeration mirrors the folder-scoped
``slot.folder_id ==`` scan, keyed on ``project_group_id``.
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from kiro_crew.dashboard.chat_folders import _effective_request_app
from kiro_crew.dashboard.chat_utils import effective_session_key
from kiro_crew.dashboard.handlers.files import _project_git_branch
from kiro_crew.dashboard.state import DashboardState
from kiro_crew.sel import sel


def _live_sessions_for_project(state: DashboardState, pid: str) -> list:
    """The live ``_ChatSlot``s tagged with ``pid`` (mirrors the folder scan)."""
    return [
        s for s in list(state._slots.values()) if getattr(s, "project_group_id", "") == pid
    ]


def _branch_for(project_dir: str) -> str:
    """Cheap current-branch label for a session's cwd (no subprocess). Empty on
    a non-repo / detached / unreadable dir — the panel just omits it then."""
    if not project_dir:
        return ""
    try:
        info = _project_git_branch(project_dir)
    except Exception:
        return ""
    if not info.get("repo"):
        return ""
    return info.get("branch", "") or ("detached" if info.get("detached") else "")


async def api_project_panel(request: web.Request) -> web.Response:
    """GET /api/projects/{id}/panel — sessions + collision flags for a project."""
    state: DashboardState = request.app["state"]
    caller = request.get("user", "dashboard")
    pid = request.match_info["id"]
    if not pid:
        return web.json_response({"error": "project id required"}, status=400)

    projects = getattr(state, "projects", None)
    record = projects.get_project(pid) if projects is not None else None
    live_slots = _live_sessions_for_project(state, pid)

    # App-ownership gate (App Kit §5.2), applied BEFORE any project data is
    # assembled or echoed. The dashboard user (no app claim) sees every project;
    # an APP caller may read a project's panel only if it owns a live session
    # tagged into that project. A caller that fails the gate — OR an unknown
    # project id — gets the SAME 404, so the endpoint is neither a name/title
    # leak nor a project-id existence oracle (Security-review Blocker + High).
    request_app = _effective_request_app(state, request)
    authorized = record is not None and (
        not request_app
        or any(getattr(s, "_app", "") == request_app for s in live_slots)
    )
    if not authorized:
        reason = "unknown project id" if record is None else "app does not own this project"
        sel().log_api_access(
            caller=caller, operation="project_panel", outcome="denied",
            resources=f"project={pid}", error=reason,
        )
        return web.json_response({"error": "not found", "code": "project_not_found"}, status=404)

    live_keys = {effective_session_key(s) for s in state._slots.values()}
    # Fork lineage (same as the flush): so the panel's collision flags exclude a
    # fork pair identically to the notify path — otherwise the panel would show
    # a same-worktree/same-file collision the notifications deliberately omit
    # (Docs/honesty: lying-status-artefact).
    _forked = {}
    for s in state._slots.values():
        parent = getattr(s, "forked_from", None)
        if parent:
            _forked[effective_session_key(s)] = parent

    def _is_fork_pair(a: str, b: str) -> bool:
        return _forked.get(a) == b or _forked.get(b) == a

    # Branch reads stat the filesystem -> off the loop. Snapshot (key, title,
    # agent, project_dir) on the loop first.
    snap = [
        {
            "session": effective_session_key(s),
            "title": getattr(s, "title", "") or "",
            "agent": getattr(s, "agent", "") or "",
            "project_dir": getattr(s, "project", "") or "",
        }
        for s in live_slots
    ]

    def _enrich_and_flag() -> dict:
        for row in snap:
            row["branch"] = _branch_for(row.pop("project_dir"))
        snap_keys = {r["session"] for r in snap}  # sessions CURRENTLY tagged into pid
        # Collision flags (design §4.4), from what the indices currently hold
        # (per-flush populated). Same-file: files this project's live sessions
        # contest. Same-worktree: trees >=2 live sessions share.
        collisions: list[dict] = []
        idx = getattr(state, "collisions", None)
        if idx is not None:
            # Scope the live set to sessions CURRENTLY tagged into this project
            # (snap_keys), NOT the global live set: a session that edited under
            # pid then RETAGGED to another project is still live and still in
            # pid's recorded rows, so counting it via global live_keys would
            # leak its id into pid's panel (Security-review: retag leak).
            for key, sessions in idx.contested_files(
                pid, live_sessions=snap_keys, is_fork_pair=_is_fork_pair
            ):
                collisions.append(
                    {
                        "signal": "same-file",
                        "repo_rel_path": key.repo_rel_path,
                        "sessions": sorted(sessions),
                    }
                )
        wt = getattr(state, "worktrees", None)
        if wt is not None:
            for root, sessions in wt.all_collisions(
                live_sessions=live_keys, is_fork_pair=_is_fork_pair
            ):
                in_project = sessions & snap_keys
                # Only surface trees a session of THIS project is in, AND only
                # list this project's own sessions — never leak a co-tenant
                # session id from another project (Security-review Blocker).
                if not in_project:
                    continue
                collisions.append(
                    {"signal": "same-worktree", "sessions": sorted(in_project)}
                )
        return {
            "project": {"id": record.id, "name": record.name, "repos": list(record.repos)},
            "sessions": snap,
            "collisions": collisions,
        }

    payload = await asyncio.to_thread(_enrich_and_flag)
    sel().log_api_access(
        caller=caller, operation="project_panel", outcome="allowed", resources=f"project={pid}"
    )
    return web.json_response(payload)
