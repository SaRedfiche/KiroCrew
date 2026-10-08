---
title: Cross-session collision signals — warn when live sessions contend on a file or worktree
status: draft
author: SaRedfiche
created: 2026-10-08
last-audited: 2026-10-08
audited-at: 791b10a68e
doc-pr: null
implementation-prs: []
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
is that design proposal. It carries no implementation PR, because — as the
reviewer established on #9743 (see below) — a `project_id`-keyed implementation
must wait for the Projects work to land.

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

### What the signal keys on

The detector groups sessions by their shared project identity. The Projects RFC
proposes a persisted `slot.project_id` as that identity, but it lives only on the
#11678 branch and is not on `main` (confirmed: `project_id` has zero hits in
`state.py` at `791b10a6`). So the design has two phases keyed on what is
available:

- **Interim (keyable today):** group by the existing `slot.project` directory
  string. Two live sessions whose `slot.project` resolves to the same directory
  are candidates for file-level contention; two sessions on the same git
  checkout are candidates for worktree-level contention.
- **Project-aware (waits on #11678):** once `slot.project_id` lands, group by it
  instead, so the signal follows the Project rather than the raw directory.

The interim key has a known blind spot, stated here rather than left for a
reviewer: `slot.project` does not follow an agent that moves into a worktree it
created mid-run (#804, #13488), so a detector keyed purely on `slot.project`
misses that session. The project-aware phase keyed on `project_id` is what
closes it; until then the worktree-level detector (below) covers the
mid-run-worktree case by keying on the checkout path, not on `slot.project`.

### Where contention is detected

- **File-level:** when a session opens or writes a file under a shared project,
  the detector checks whether another live session scoped to the same project is
  touching the same path, and records the contention.
- **Worktree-level:** on the `POST /api/worktree/create` path, and on any
  session binding to an existing checkout, the detector checks whether another
  live session already holds that checkout. This is the "worktree already active
  in another session" case #1607 names.

### How the warning surfaces

The warning is non-blocking and advisory. The surface is the one undecided
product choice carried into *Open questions*: either a dashboard panel/affordance
that shows "another session is editing this file / holds this worktree", or the
agent-facing `send_notification` path. For reference, `send_notification` is the
agent-facing MCP tool that posts to the notification center. The RFC does not
pick between them; it records both and asks the owner to decide.

### Discovery primitive

A session needs to answer "who else has this project open, live?" to compute
contention. Two shapes exist and the design reuses rather than duplicates:

- `list_sessions` (`src/kiro_crew/mcp_tools/sessions.py:398`) today returns no
  project directory and no live marker. Adding a project/live marker to its rows
  is the smallest discovery change.
- `chat_folder_tree` on the opt-in dashboard server lists live sessions with a
  running flag, grouped by sidebar folder with each folder's project directory.
  Its grouping follows how the person filed sessions (#6490), not each session's
  own `slot.project`, so it is a reference for the live-marker shape but not a
  drop-in project-scoped discovery.

Discovery stays owner-scoped: it reports peers to the person, and does not expose
a control handle on them.

## Migration plan

Additive and phased. Phase 1 ships the interim detector keyed on `slot.project`
plus the discovery marker and the advisory warning, independent of #11678. Phase
2 re-keys the detector on `slot.project_id` once #11678 merges, closing the
mid-run-worktree blind spot. No existing behaviour changes in either phase for a
session that does not share a project with a live peer.

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

- **A new `project_group_id` field.** Rejected: it is a second, overlapping
  project concept beside the Projects RFC's `project_id`. Reusing `project_id`
  keeps one grouping identity; the cost is that the Project-aware phase waits on
  #11678.
- **A blocking worktree gate instead of a warning.** Deferred to #1607, which
  owns "warn or refuse". This RFC proposes the warning; the refuse decision is
  not ours to make here.
- **Letting one session steer a contending peer.** Rejected as out of scope: it
  requires widening `_caller_is_ownership_fenced`, a separate session-control
  decision.

## Consequences

- A person running two sessions on one project gets told when they converge on a
  file or a checkout, before it becomes a conflict or a lost edit.
- The design is sequenced behind #11678 for its Project-aware form, and overlaps
  #1607 for the worktree check. It can ship an interim form keyed on
  `slot.project` without waiting, accepting the mid-run-worktree blind spot until
  Phase 2.

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
4. **Warning surface:** dashboard panel/affordance vs `send_notification`. A
   product call.

Deciders: **bolichen97** (RFC reviewer who triaged #9743), and the owner of
[`rfc-crew-projects.md`](rfc-crew-projects.md) / #11678 (**kseam**).

## Resolved decisions

None yet. This document is `draft`; the status flips to `accepted` when a
maintainer records the decision here, and no `project_id`-keyed implementation
PR opens until #11678 merges.
