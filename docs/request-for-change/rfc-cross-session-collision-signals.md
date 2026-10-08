---
title: Cross-session collision signals — warn when live sessions contend on a file or worktree
status: draft
author: SaRedfiche
created: 2026-10-08
last-audited: 2026-10-08
audited-at: 791b10a68e
doc-pr: null
implementation-prs: []
fork-implementation: SaRedfiche/KiroCrew@feature/collision-signals
plan-doc: docs/request-for-change/plans/2026-09-30-rebaseline-thin-project-salvage-collision-signals.md
tracking-issues: [9743, 15584, 1607]
supersedes: []
superseded-by: []
---

# RFC: Cross-session collision signals — warn when live sessions contend on a file or worktree

Two agent sessions scoped to the same project can edit the same file, or drive
the same git worktree, with neither one aware of the other. Nothing on `main`
detects that contention or surfaces it, so the hazard shows up only after the
fact as a merge conflict, a clobbered edit, or a worktree in an unexpected
state. This RFC records a design for a cross-session *collision signal*: a
read-only detector that notices when live sessions contend on a shared file or
worktree, and a non-blocking warning that surfaces it to the person before the
damage lands.

It is deliberately a signal layer, not a new grouping concept. It answers open
question 8 of [`rfc-crew-projects.md`](rfc-crew-projects.md) — "does cross-session
shared context need any surface in v1?" — from the contention-safety side, and
it reuses whatever grouping identity the Projects design lands rather than
introducing a parallel one.

## Summary

Give sessions that share a project (or a worktree) a way to detect that another
live session is working the same file or checkout, and surface a non-blocking
warning. The detector is read-only: it discovers peers and reports contention,
and does not let one session control another. The warning is advisory: it never
blocks an edit or a worktree create, it only tells the person that two sessions
are converging on the same thing.

This is scoped as the answer to the contention half of `rfc-crew-projects.md`
open question 8. It is **not** a second Project concept, **not** a widening of
the sibling ownership fence, and **not** a per-session worktree lifecycle — each
of those is owned by a separate decision named under *Non-goals* and *Open
questions*.

## Motivation

### Current state

Everything below was verified against `main` at
[`791b10a6`](https://github.com/kirodotdev/KiroCrew/commit/791b10a68e0b0070073379851727718a162928a8).

- A session's project is a single directory string. `_ChatSlot` carries
  `self.project: str = ""` and no grouping field
  (`src/kiro_crew/dashboard/state.py:3018`). There is no `project_id`:
  `git grep project_id` in `state.py` returns nothing.
- There is no project-scoped discovery. `list_sessions`
  (`src/kiro_crew/mcp_tools/sessions.py:398`) returns a caller's own sessions
  with title, agent, message count and creation time, with no project directory
  and no live marker, so one session cannot ask "who else has this project
  open?".
- The only worktree entry point is `POST /api/worktree/create`
  (`src/kiro_crew/dashboard/handlers/worktree.py:3`, handler at L761), which
  creates a sibling worktree. Nothing on that path checks whether another
  session already holds a worktree on the same checkout.
- Sibling sessions cannot address each other. `_caller_is_ownership_fenced`
  (`src/kiro_crew/dashboard/session_control.py:366`) refuses any attempt by one
  session to control a session it did not itself create, so even two siblings
  from one parent are fenced off from each other.

### Problems

- Two sessions scoped to the same project can edit the same file at the same
  time, and the first either learns about it as a merge conflict or silently
  loses the edit. Neither session is warned.
- Two sessions can share one git checkout, or one session can create a worktree
  on a checkout another session is already driving, with no signal that the
  checkout is contended.
- [#1607](https://github.com/kirodotdev/KiroCrew/issues/1607) already lists
  "warn or refuse when a worktree is already active in another session" as
  unstarted work. The same-worktree signal this RFC proposes is the first half
  of that item.

### Why this needs an accepted RFC

The maintainer triage bot declined to merge an outside pull request for this on
both [#9743](https://github.com/kirodotdev/KiroCrew/issues/9743#issuecomment-6052655828)
and [#15584](https://github.com/kirodotdev/KiroCrew/issues/15584), with the same
reason: "This adds a new concept. The team designs these itself... To propose a
design, follow the request-for-change process in CONTRIBUTING.md." This document
is that design proposal. A Phase 1 implementation exists on a fork lane
(`SaRedfiche/KiroCrew@feature/collision-signals`, with the plan doc named in the
front matter), but **no upstream implementation PR is opened**: as the reviewer
established on #9743 (see below), re-pointing the peer-scope source to
`project_id` must wait for the Projects work to land, and the maintainer ruling
is that the design is argued over here first.

## Goals

- Detect, read-only, when two or more live sessions contend on the same file or
  the same git worktree.
- Surface the contention as a non-blocking, advisory warning to the person.
- Key the detector on whatever grouping identity the Projects design lands,
  rather than on a new field this RFC invents.
- Add a project-scoped discovery primitive (a session can learn which other
  live sessions share its project) without granting any control over them.

## Non-goals

- A new Project or grouping concept parallel to the Projects RFC's `project_id`.
  A separate `project_group_id` would be a second, overlapping project concept;
  this RFC reuses the Projects identity instead (see *Alternatives considered*).
- Widening the sibling ownership fence (`_caller_is_ownership_fenced`). Letting
  one session act on a peer is a separate session-control decision, flagged on
  this RFC's *Open questions* and raised earlier on #4333. This RFC's signal is
  discovery and warning only.
- A per-session worktree lifecycle. Whether sessions on one Project share one
  checkout or get per-session worktrees is the "shared-checkout vs per-session"
  choice open on [#11678](https://github.com/kirodotdev/KiroCrew/pull/11678) and
  tracked for the worktree side by #1607. This RFC consumes whatever that choice
  lands; it does not make it.
- Blocking or refusing an edit or a worktree create. The signal is advisory. The
  server-side worktree check stays whatever #1607 decides; this RFC adds a
  warning, not a gate.

## Design

This section describes the design as implemented. A Phase 1 implementation of
the two primary signals ships on the branch that carries this document
(`src/kiro_crew/dashboard/collision_index.py`, `collision_derive.py`,
`collision_notify.py`, with the same-worktree signal and a `remote_target`
third signal on the lane), so the design below is grounded in shipped code, not
sketched. The modules organize themselves against an internal
peer-coordination design numbered §4.2 (the signals) and §4.4 (notify), which
this RFC supersedes as the design of record.

### The three signals

Three distinct contention events, each a concrete "two live sessions racing on
the same X":

1. **Same-file (Signal 1).** Two or more DISTINCT live sessions, scoped to the
   same project, editing the same repo-relative file within a recency window
   (30 minutes). A textual conflict is a same-file event regardless of branch,
   so this is the primary everyday merge-risk signal. Implemented in
   `collision_index.py` (`CollisionIndex`, keyed on `(repo_id,
   repo_rel_path)`); collision is `COUNT(DISTINCT session) >= 2` for a key
   within the window.
2. **Same-worktree (Signal 2).** Two live sessions driving the same git
   checkout — a live filesystem race. This is the "worktree already active in
   another session" case #1607 names.
3. **Same-remote (Signal 3).** Two sessions whose push target is the same
   `canonical_url#ref` — i.e. about to contend on one remote PR/branch.
   Derived off-loop in `collision_derive.py`.

### Keying: `repo_id`, not the worktree toplevel

The signal key is derived, not the raw directory. `collision_derive.py` computes
`repo_id` = the canonicalized `origin` remote URL when present, else
`git rev-parse --git-common-dir` — deliberately **not** `worktree_root`, because
two worktrees of one repo share an origin and a common dir but have different
toplevels, so keying on the toplevel would defeat the multi-worktree match.
`repo_rel_path` is `relpath(realpath(file), realpath(repo_root))` (both
realpath'd so a symlinked macOS `/tmp`→`/private/tmp` worktree root still
compares). An out-of-tree write (a path not under the repo root) is dropped
rather than given a manufactured `../..` key, and a git failure or non-repo path
returns `None` and is dropped — a derive miss just means no entry, never a raise
into the caller.

This derivation is the reason the mid-run-worktree blind spot (#804, #13488)
does not sink the design: the signal keys on the derived `repo_id` of the file's
actual checkout, not on `slot.project`, so a session that moved into a worktree
it created mid-run is still keyed correctly.

### The grouping key is decoupled: `coordination_scope_id` → `project_id`

The signals must not hard-code *what kind of grouping* makes two sessions peers.
The implementation threads one neutral opaque field — a coordination scope id —
that answers only "which sessions are collision peers", sourced from whatever
binding the runtime has:

- **Today / interim:** the existing project-coordination tag on the slot. The
  signals work on the current branch unchanged.
- **When Projects PR A / #11678 lands:** the thin Project's `slot.project_id`.
  This is a one-line change at the *source* site, not in the signal code —
  `project_id` has zero hits in `state.py` at `791b10a6`, so there is nothing to
  key on upstream yet, which is exactly why the field is kept neutral.
- **Fallback:** empty scope → the session is unscoped → never indexed (already
  the signals' contract for an untagged session).

A later revision of this design (realized on the lane) went further and dropped
the separate scope component from the dedupe signature entirely, keying each
signal on its concrete contended thing (`repo_id/repo_rel_path`, the worktree
root, the `remote_target`), because that fully-qualified thing already carries
all the scoping two peers need. The scope id remains only as the "are these
sessions peers at all" gate at the source site.

### Hot-path discipline

The same-file index is in-memory, process-local runtime state — **not** a
durable `*.json` store. Edit events are ephemeral and evaluated against a
recency window and the *currently live* session set; a gateway restart has no
live sessions, so nothing here is meaningful to persist. The append happens on
the hot turn path (per file-tool write), where disk/subprocess work is
forbidden, so all git derivation runs OFF the event loop in the per-turn flush
before entries reach the index. `CollisionIndex` owns only the pure synchronous
index math (record, prune by recency, "which files are contested by ≥2 distinct
live sessions now"); liveness and fork-lineage are supplied by the caller, so
the module stays a unit-testable leaf that imports no dashboard state.

### Notify decisions and noise discipline (§4.4)

The two file/worktree signals feed one notification path with strict noise
discipline, because a notification per same-file overlap would fire on the files
everyone touches (`__init__.py`, lockfiles, a barrel/router) and the user would
mute it within a day. `collision_notify.py` implements:

- **Same-worktree notifies by default** — rare and high-severity.
- **Same-file is record-first.** Every same-file edit is recorded and queryable,
  but it notifies ONLY under suppression: a runtime contested-ness threshold (a
  file touched by more than `k` distinct sessions is a high-churn shared file,
  self-evidently not a two-way merge risk, and is never notified), an optional
  static high-churn allowlist pre-seed, and **notify-once per key** so a pair is
  never re-pinged.
- **The two signals are deduped:** a same-worktree collision that is also
  same-file emits ONE notification.

The module is pure decision + dedupe state; it does not call `send_notification`
itself — the caller does, with the returned body.

### What the notification body carries (security-shaped)

The notification body and `group_key` carry **no scope value at all** — not the
raw scope, not a hash of it. Notifications persist to the agent-readable,
unscoped `notifications.jsonl`, and the scope defaults to the session's
`repo_id` (an internal `host/org/repo` identity or an absolute home-dir path),
which is neither opaque nor high-entropy, so a hash of it would be
dictionary-attackable against enumerable repo names. Rather than obfuscate a
guessable identity, nothing scope-derived is persisted: the body states only the
signal type and the session count, and notes group by signal type
(`collision:same-worktree` / `collision:same-file`). A reader resolves which
sessions are involved from live session state, out of band; the body carries no
name and no click-through.

### Discovery primitive

A reader needs to answer "who else has this project open, live?" to resolve a
notification to concrete sessions. Two existing shapes are reused rather than
duplicated:

- `list_sessions` (`src/kiro_crew/mcp_tools/sessions.py:398`) today returns no
  project directory and no live marker. Adding a project/live marker to its rows
  is the smallest discovery change.
- `chat_folder_tree` on the opt-in dashboard server lists live sessions with a
  running flag, grouped by sidebar folder with each folder's project directory.
  Its grouping follows how the person filed sessions (#6490), not each session's
  own `slot.project`, so it is a reference for the live-marker shape but not a
  drop-in project-scoped discovery.

Discovery stays owner-scoped: it reports peers to the person and exposes no
control handle on them. A panel consumer of the same-file record is future work;
none ships on the current branch.

## Migration plan

Additive and phased, matching what is built.

- **Phase 1 (shipped on this branch):** the same-file index, the same-worktree
  signal, the `remote_target` signal, off-loop git derivation, and the
  record-first notify path with its noise discipline — all keyed through the
  neutral coordination scope sourced from the current project-coordination tag.
- **Phase 2 (waits on #11678):** re-point the scope id's *source site* to
  `slot.project_id` when the thin Project binding lands. No change to the signal
  modules themselves.

No existing behaviour changes in either phase for a session that is unscoped or
does not share a project with a live peer.

## Backward compatibility

Compatible. The discovery marker is an additive field on `list_sessions` rows;
callers that ignore it are unaffected. The warning is a new advisory surface and
blocks nothing. Sessions not sharing a project with a live peer see no change.
The server-side worktree behaviour is untouched by this RFC.

## Security considerations

Peer discovery reveals "who else has this project open", which is information
about the person's other sessions. Keep it owner-scoped, matching the #11678
per-Project session list, which is an owner-only dashboard endpoint with no
agent-facing tool. The signal must not become a channel for one session to learn
the *contents* of another's work, only that a named path or checkout is
contended.

## Alternatives considered

- **The group-shared-memory model (built, then superseded).** An earlier
  revision of this feature grouped sessions under a `project_group_id` with a
  shared group memory pool, and the collision signals were threaded through that
  field. `rfc-crew-projects.md` Revision 2 ("the thin Project", 2026-09-18)
  walked away from group-shared-memory in favour of one store per Project bound
  at session creation, and listed shared-memory-across-Projects as open question
  8. The group-memory core was therefore parked. What survived is exactly these
  collision signals, which are model-agnostic: two sessions racing on a file,
  worktree, or remote target is a real hazard whether the memory model is "a
  group sharing a pool" or "one thin Project a session binds to". The salvage
  decoupled the signals from the retired field via the neutral coordination
  scope id (see Design), so a `project_group_id` is not reintroduced.
- **A blocking worktree gate instead of a warning.** Deferred to #1607, which
  owns "warn or refuse". This RFC proposes the warning; the refuse decision is
  not ours to make here.
- **Letting one session steer a contending peer.** Rejected as out of scope: it
  requires widening `_caller_is_ownership_fenced`, a separate session-control
  decision.
- **Persisting the same-file index to disk.** Rejected: the data is ephemeral
  edit events against a recency window and the live-session set, meaningless to
  persist across a restart (see *Hot-path discipline*).

## Consequences

- A person running two sessions on one project gets told when they converge on a
  file, a checkout, or a remote push target, before it becomes a conflict or a
  lost edit.
- Because the signals key on a derived `repo_id` rather than on `slot.project`,
  they already handle multi-worktree and mid-run-worktree cases; the Projects
  binding is needed only to decide *which sessions are peers*, not to key the
  contention itself.
- The design is sequenced behind #11678 only for re-pointing the peer-scope
  source to `slot.project_id`, and overlaps #1607 for the worktree check. The
  Phase 1 signals run today on the interim coordination tag without waiting.

## Open questions

The following need a maintainer ruling; the first three are the rulings the
reviewer on #9743 said are still owned by the Projects RFC / #11678 owner and
session-control owner.

1. **Does open question 8 warrant any v1 surface at all?** `rfc-crew-projects.md`
   OQ8 is still open and asks whether one-store-per-Project covers every real
   consumer. If the answer is "no surface in v1", this RFC's Project-aware phase
   does not start.
2. **Shared-checkout vs per-session worktree within a Project.** The worktree-
   level detector's shape depends on which model #11678 lands. Owned by the
   Projects RFC / #11678.
3. **Widening the sibling ownership fence.** Out of scope for this RFC, but the
   related ask on #4333 is adjacent. Owned by session-control.
4. **Warning surface — partially settled by the implementation.** Phase 1
   notifies via the unscoped agent-readable `notifications.jsonl` with a
   scope-free body (see Design); a dashboard panel consumer of the same-file
   record is deliberately left as future work. What remains a maintainer product
   call is whether a panel surface should ship and, if so, whether it replaces
   or supplements the notification path.

Deciders: **bolichen97** (RFC reviewer who triaged #9743), and the owner of
[`rfc-crew-projects.md`](rfc-crew-projects.md) / #11678 (**kseam**).

## Resolved decisions

None yet. This document is `draft`; the status flips to `accepted` when a
maintainer records the decision here. A Phase 1 implementation of the signals
already exists on the fork lane named in the front matter; what waits on #11678
is re-pointing the peer-scope source to `slot.project_id` and opening any
upstream PR, neither of which happens before the Projects binding lands and this
RFC is accepted.
