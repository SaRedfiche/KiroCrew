"""HTTP routes for project-group shared memory (design §12.6).

The two ``group_memory_read`` / ``group_memory_write`` MCP tools on
``kirocrew-dashboard`` land here. The security contract is the one
:mod:`kiro_crew.dashboard.handlers.work_ledger` and ``session_ledger`` state:
WHICH group's memory a request touches is derived from the CALLING SESSION's own
slot — its ``project_group_id`` — never from the request body. There is no
``project_group_id`` parameter on either tool, so a caller cannot name another
group's store; an out-of-group read or write is unrepresentable rather than
validated away.

Two routes, both MCP-only (no browser caller), listed under
``server._STRICT_INTERNAL_API_PATHS`` by their shared ``/api/group-memory``
prefix — without that entry the internal-secret call falls through to cookie
auth and every tool call fails with 403 before this module's own recognition
can run.

Read vs write authority (§12.6):

* **read** — any session tagged into the group. The shared memory is background
  the group already works against, so every member sees it (it is auto-injected
  as a context tier for exactly these sessions anyway).
* **write** — the human, and a COORDINATOR session. "Coordinator" is the same
  derivation the project panel uses: a session that owns a readable conductor
  work-ledger (``work_ledger.read_conductor`` on its effective key). A plain
  member session may read but not write, so one worker cannot rewrite the
  project context every sibling then ingests. The human writes through the
  dashboard, not this MCP surface.

Restricted (incognito / temporary / guest) sessions are refused: group memory is
durable on-disk state, which those modes promise not to leave behind. A
temporary session that ``blocks_reads`` is refused the read too.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from kiro_crew import group_memory, work_ledger
from kiro_crew.dashboard.chat_utils import effective_session_key
from kiro_crew.dashboard.handlers._shared import (
    _blocks_reads_session,
    _is_restricted_session,
    _read_session_key,
)
from kiro_crew.dashboard.handlers.cron import _recognize_session
from kiro_crew.dashboard.state import DashboardState
from kiro_crew.history import is_incognito_transcript
from kiro_crew.platform import redact_via_context as redact
from kiro_crew.validation import (
    GROUP_MEMORY_WRITE_SCHEMA,
    ValidationError,
    validate_tool_args,
)

logger = logging.getLogger(__name__)

#: Modes ``group_memory_write`` accepts (mirrors the schema's allowed set). A
#: missing mode defaults to ``append`` — the safe default for a coordinator
#: adding project context, since it cannot silently discard the accumulated blob.
_DEFAULT_WRITE_MODE = "append"

#: A visible separator between appended entries so the injected blob reads as
#: distinct notes rather than one run-on paragraph.
_APPEND_SEPARATOR = "\n\n"


def _sel():
    """Late-binding ``sel()`` for test monkeypatch compatibility."""
    import kiro_crew.dashboard.handlers as _pkg

    return _pkg.sel()


def _audit(caller: str, operation: str, outcome: str, resources: str = "", error: str = "") -> None:
    """Enqueue one SEL row for a group-memory touch. Never changes the outcome."""
    try:
        _sel().log_api_access(
            caller=caller,
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
            error=error,
        )
    except Exception:  # pragma: no cover - audit must never change the outcome
        logger.debug("SEL audit for %s failed", operation, exc_info=True)


def _refuse(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": message, "code": code}, status=status)


async def _caller_group(
    request: web.Request, operation: str
) -> tuple[tuple[str, str], None] | tuple[None, web.Response]:
    """Vet the caller and resolve its ``(effective_key, project_group_id)``.

    Returns ``((effective_key, group_id), None)`` or ``(None, refusal)``. The
    group id comes from the CALLER's own slot and from nowhere else — that is
    what makes the tools' group identity unforgeable, exactly as the work-ledger
    handler derives a worker's item from its binding rather than a parameter.

    Refusals, in order: a non-internal (cookie) caller cannot name a session; an
    unrecognized session key; a restricted session (durable state); a caller not
    tagged into any project group.
    """
    if not request.get("internal_auth"):
        # Positively confirmed internal-secret principal — NOT inferred from the
        # path being strict. On loopback a strict path with no secret header
        # falls through to cookie auth and is granted, reaching here with
        # ``internal_auth`` unset; ``_recognize_session`` would then accept any
        # ``X-Session-Key`` naming a known session without tying it to the
        # authenticated principal, so a cookie-authed loopback caller could name
        # another session's key and read or write its group memory. All routes
        # are MCP-only by design, so this costs nothing reachable.
        _audit(
            request.headers.get("X-Session-Key", "") or "anonymous",
            operation,
            "denied",
            resources="cookie_caller_block",
            error="internal_auth_required",
        )
        return None, _refuse(
            403,
            "internal_auth_required",
            "Group memory is reachable only by an agent's MCP tools, which "
            "authenticate with the gateway's internal secret. A browser session "
            "cannot name which group it is.",
        )
    state: DashboardState = request.app["state"]
    sk = _read_session_key(request)
    refusal = await _recognize_session(
        state, sk, operation, blocks_persisted_mode=is_incognito_transcript
    )
    if refusal is not None:
        return None, refusal
    if _is_restricted_session(state, request):
        _audit(sk, operation, "denied", resources="restricted_session_block")
        return None, _refuse(
            403,
            "restricted_session",
            "Group memory is not available in this session mode.",
        )

    slot, effective_key = _resolve_slot(state, sk)
    if slot is None:
        _audit(sk, operation, "denied", error="unknown_session")
        return None, _refuse(
            404,
            "unknown_session",
            "This session is not open, so its project group cannot be resolved.",
        )
    group_id = str(getattr(slot, "project_group_id", "") or "")
    if not group_id:
        _audit(effective_key, operation, "denied", error="not_tagged")
        return None, _refuse(
            404,
            "not_tagged",
            "This session is not tagged into a project group, so it has no shared "
            "memory. Tag the session into a project first.",
        )
    return (effective_key, group_id), None


def _resolve_slot(state: DashboardState, sk: str):
    """Find the caller's slot and its effective key from ``X-Session-Key``.

    The slot table is keyed by the bare slot name; a session key may carry a
    ``dashboard:`` (or channel) prefix, so both the raw and stripped forms are
    tried. The returned effective key is what a turn actually runs as — the same
    key the conductor work-ledger is written under — so the coordinator check
    below tests the identity the ledger would answer for.
    """
    if not sk:
        return None, ""
    slot_name = sk.split(":", 1)[-1] if ":" in sk else sk
    for candidate in (sk, slot_name):
        slot = state._slots.get(candidate)
        if slot is not None:
            return slot, effective_session_key(slot)
    return None, ""


def _is_coordinator(effective_key: str) -> bool:
    """Whether *effective_key* owns a readable conductor work-ledger.

    The same derivation the project panel uses for its ``is_coordinator`` flag:
    a session is a coordinator iff ``read_conductor`` returns a record for its
    effective key. Lock-free and torn-file safe — an unreadable ledger reads as
    "not a coordinator", never a crash.
    """
    if not effective_key:
        return False
    try:
        return work_ledger.read_conductor(effective_key) is not None
    except Exception:  # pragma: no cover - a ledger read must never 500 the route
        logger.debug("coordinator probe failed for %s", effective_key, exc_info=True)
        return False


async def api_group_memory_get(request: web.Request) -> web.Response:
    """GET /api/group-memory — this session's project-group shared memory.

    Returns the raw stored text (or empty when nothing is stored yet) for the
    group the CALLER is tagged into. Any tagged member may read.
    """
    if _blocks_reads_session(request.app["state"], request):
        sk = _read_session_key(request)
        _audit(sk, "group_memory_read", "denied", resources="blocks_reads_block")
        return _refuse(
            403,
            "restricted_session",
            "Group memory reads are not available in this session mode.",
        )
    resolved, refusal = await _caller_group(request, "group_memory_read")
    if refusal is not None:
        return refusal
    assert resolved is not None
    effective_key, group_id = resolved
    try:
        text = await asyncio.to_thread(group_memory.GroupMemoryStore(group_id).read)
    except group_memory.GroupMemoryError as exc:
        _audit(effective_key, "group_memory_read", "denied", resources=group_id, error=str(exc))
        return _refuse(400, "invalid_group", "the project group id is not usable")
    except OSError:
        logger.warning("group memory read failed for %s", group_id, exc_info=True)
        return _refuse(503, "read_failed", "group memory read failed; try again")
    _audit(effective_key, "group_memory_read", "ok", resources=group_id)
    # The stored text is human/coordinator-authored and re-injected into context;
    # redact on the way out as well, matching the work-ledger read.
    return web.json_response({"memory": redact(text), "group_id": group_id})


async def api_group_memory_write(request: web.Request) -> web.Response:
    """POST /api/group-memory — write this session's project-group shared memory.

    Coordinator-only: a plain member session is refused so it cannot rewrite the
    context every sibling ingests. ``mode`` is ``append`` (default) or
    ``replace``; ``text`` is required and capped by the schema.
    """
    resolved, refusal = await _caller_group(request, "group_memory_write")
    if refusal is not None:
        return refusal
    assert resolved is not None
    effective_key, group_id = resolved

    if not _is_coordinator(effective_key):
        _audit(effective_key, "group_memory_write", "denied", resources=group_id, error="not_coordinator")
        return _refuse(
            403,
            "not_coordinator",
            "Only a coordinator session (one that owns a work ledger) may write "
            "project-group shared memory. A member session reads it; the human and "
            "the coordinator maintain it.",
        )

    try:
        body = await request.json()
    except Exception:
        return _refuse(400, "invalid_json", "invalid JSON")
    if not isinstance(body, dict):
        return _refuse(400, "invalid_body", "request body must be a JSON object")
    try:
        cleaned = validate_tool_args(
            {k: v for k, v in body.items() if v is not None}, GROUP_MEMORY_WRITE_SCHEMA
        )
    except ValidationError as exc:
        return _refuse(400, "invalid_value", str(exc))

    text = str(cleaned.get("text") or "")
    mode = str(cleaned.get("mode") or _DEFAULT_WRITE_MODE)

    try:
        stored_len = await asyncio.to_thread(_apply_write, group_id, text, mode)
    except group_memory.GroupMemoryError as exc:
        _audit(effective_key, "group_memory_write", "denied", resources=group_id, error=str(exc))
        return _refuse(400, "invalid_group", "the project group id is not usable")
    except OSError:
        logger.warning("group memory write failed for %s", group_id, exc_info=True)
        return _refuse(503, "write_failed", "group memory write failed; try again")

    _audit(effective_key, "group_memory_write", "ok", resources=f"{group_id} mode={mode}")
    return web.json_response({"ok": True, "group_id": group_id, "mode": mode, "chars": stored_len})


def _apply_write(group_id: str, text: str, mode: str) -> int:
    """Apply an append/replace write and return the new stored length.

    Append reads the current blob and concatenates with a visible separator;
    replace overwrites. Both go through the store's atomic write. Runs off the
    event loop (blocking file I/O).
    """
    store = group_memory.GroupMemoryStore(group_id)
    if mode == "replace":
        new_text = text
    else:  # append (default)
        existing = store.read().rstrip()
        new_text = (existing + _APPEND_SEPARATOR + text) if existing else text
    store.write(new_text)
    return len(new_text)
