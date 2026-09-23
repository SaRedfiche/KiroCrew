# Coordination Panel — Converge on the Crew-Log Substrate

> **Active plan.** Written 2026-09-23 after rebasing
> `feature/project-group-shared-memory` onto an origin/main that shipped the
> crew-log projection kernel, the work-ledger board fold, and the session-tree
> (conductor lane) projection. The coordination panel (§12.6) currently
> hand-rolls three things main now provides. This plan converges the panel onto
> that substrate instead of maintaining a parallel implementation.

**Goal:** Stop the coordination panel from carrying its own copies of machinery
main already owns — fold work items out of the crew log rather than a parallel
store, serve the panel from a commit-driven projection rather than a per-poll
rescan, and reuse the session-tree projection rather than recompute a session
list. Each step is its own branch and its own ship-it gate.

**Branch:** `feature/project-group-shared-memory` (fork SaRedfiche/KiroCrew).
Rebased tip at authoring time: `f73f10caf`. Backup: `backup/pre-rebase-sep23`.

**Substrate we are building on (verified in the rebased tree):**
- `src/kiro_crew/crew_log/projection.py` — incremental fold-with-checkpoint over
  ONE session's append-only crew log; serves the five side-panel folds plus the
  `work` and `ledger` folds. Checkpoint state is JSON, resumable by `seq`.
- `src/kiro_crew/work_ledger.py` — `rebuild_from_projection()` (~L2649) folds a
  work board from `read_slot_projection(slot, "work", also_slots=…)` via
  `work_slots_naming_board`. This is the existing single source of truth for work items.
- `src/kiro_crew/crew_log/session_tree_projection.py` — the conductor lane: an
  in-memory session tree advanced by the writer at commit, read with zero disk
  on the 5s poll, checkpoint-backed, returns the same object identity while
  unchanged.
- Our surface today: `src/kiro_crew/dashboard/project_panel.py`
  (`api_project_panel` rescans `state._slots.values()` every poll),
  `website/src/components/CoordinationPanel.tsx`.

## Global Constraints

- Every step lands on its own branch off the current tip, granular test-green
  commits, staged by explicit path (never `git add .`/`-A`).
- Every step goes through the ship-it gate (ASH + adversarial crew + multimodel
  panel) before it joins the branch. Frontend-only deltas may skip Holmes with
  the recorded rationale.
- Do not fork a second copy of anything main owns. If the substrate is missing a
  hook we need, extend the substrate (upstreamable), don't shadow it in the panel.
- Verify against source at each step — the rebase proved commit messages
  under-describe the real shape.

---

## Step 1 — Fold work items from the crew-log `work` projection (HIGHEST VALUE)

Close the biggest divergence: the panel's WORK section reads our own store; main
folds the same concept from the crew log. Re-point the panel at the crew-log
`work` projection for the coordinator's slot(s), retiring the parallel store.

- [ ] Read `work_ledger.rebuild_from_projection` + `work_slots_naming_board` and
      the `work` fold in `crew_log/projection.py`; document the exact shape the
      fold emits (item id, title, assignee, status, epoch).
- [ ] Map our panel's work-item fields onto the fold's fields; identify any field
      the fold does not carry (assignee-to-a-named-worker is the likely gap).
- [ ] If a gap exists, decide extend-the-fold (upstreamable) vs derive-in-panel;
      prefer extending the fold so one reader owns the shape.
- [ ] Change `api_project_panel`'s work section to read
      `read_slot_projection(coordinator_slot, "work", also_slots=member_slots)`
      instead of the parallel store.
- [ ] Delete the parallel work-item store + its writes once nothing reads it;
      grep to prove zero readers before deletion.
- [ ] Tests: the panel's work section reflects a crew-log-recorded work item;
      resume-by-seq equivalence holds (fold from checkpoint == fold from scratch).
- [ ] Update the feature-map Coordination row's handler/endpoint cells if the
      backing source moved.
- [ ] Ship-it gate; commit.

## Step 2 — Serve the panel from a commit-driven projection (not a per-poll rescan)

Our `api_project_panel` iterates `state._slots.values()` and re-derives group
membership + collisions on every 5s poll — exactly what
`session_tree_projection.py` was written to eliminate. Model the group view as a
commit-driven projection with a checkpoint, same three rules (pure fold driven
eagerly at commit; same reference while unchanged; checkpoint is a shortcut,
never authority).

- [ ] Study `session_tree_projection.py` as the template (apply-at-commit,
      identity-stable `nodes()`, `ver`-mismatch discards, cold-rebuild fallback).
- [ ] Define the project-group projection state (membership by group, collision
      edges) and the deltas that advance it (tag write, untag, worktree change).
- [ ] Wire `apply` into the same commit points that already push slot updates
      (`push_slots_update` / tag-write path), durability-first.
- [ ] `api_project_panel` reads the projection; returns stable identity while
      unchanged so the 5s poll does zero work when nothing moved.
- [ ] Keep a cold-rebuild path (the current rescan) as the fallback the checkpoint
      defers to, not the per-read path.
- [ ] Tests: no disk read on an unchanged poll; a tag write advances the
      projection; checkpoint staleness falls back to cold rebuild without a wrong answer.
- [ ] Ship-it gate; commit.

## Step 3 — Reconcile the MEMBERS view with the conductor lane (#12757)

Main's conductor lane nests sessions under the one that opened them (spawn
lineage); our MEMBERS section lists sessions tagged into a group (tag grouping).
These are different questions, so likely BOTH survive — but the panel should
reuse the session-tree projection for the session facts it needs rather than
compute its own list.

- [ ] Read the conductor-lane feature (#12757) and `SessionTree` /
      `session_tree_projection` to see what session facts it already exposes.
- [ ] Decide: keep MEMBERS as a distinct tag-grouped view (recommended) vs fold
      it into the lane. Record the decision with its reason.
- [ ] If kept: have MEMBERS read session facts (title, branch, status) from the
      session-tree projection instead of re-deriving from `_slots`.
- [ ] Tests + ship-it gate; commit.

## Deferred / independent (not blocking the convergence)

- [ ] `window.prompt` → inline input in `CoordinationEmptyState` (both UX lanes
      flagged it round-2; non-blocking).
- [ ] Diagnose the in-branch tag-refresh NO-GO (`ac32175a2`, undiagnosed) — needs
      a pod repro; the misdirected `['chat-slots']` invalidation is at best a no-op.

## Order rationale

1 first: it is the largest divergence and the one that most keeps us aligned with
upstream (one work-item source of truth). 2 next: it removes the per-poll rescan
now that 1 has proven we can read from projections. 3 last: it depends on
understanding both the lane and our own view, and is mostly a decision plus a
read-source swap. The two deferred items are independent and can slot in whenever.
