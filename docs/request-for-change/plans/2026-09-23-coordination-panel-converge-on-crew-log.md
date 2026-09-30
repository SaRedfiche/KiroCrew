# Coordination Panel — Converge on the Crew-Log Substrate

> **SUPERSEDED 2026-09-30** by
> [`2026-09-30-rebaseline-thin-project-salvage-collision-signals.md`](2026-09-30-rebaseline-thin-project-salvage-collision-signals.md).
> This plan's premise — §12.6 project-group *shared memory*, converged onto the
> crew-log substrate — was already retired when it was written: `rfc-crew-projects.md`
> shipped **Revision 2 ("the thin Project")** on 2026-09-18, five days earlier,
> walking away from the group-shared-memory model. The collision signals survive
> and are salvaged under the new plan; the panel-convergence Steps 1–3 are
> dropped (the panel is downstream of the memory model the RFC retired). Kept
> here for history.

> **Active plan (historical).** Written 2026-09-23 after rebasing
> `feature/project-group-shared-memory` onto an origin/main that shipped the
> crew-log projection kernel, the work-ledger board fold, and the session-tree
> (conductor lane) projection. The coordination panel (§12.6) currently
> hand-rolls three things main now provides. This plan converges the panel onto
> that substrate instead of maintaining a parallel implementation.

**Goal:** Stop the coordination panel from carrying its own copies of machinery
main already owns — fold work items out of the crew log rather than a parallel
store, serve the panel from a commit-driven projection rather than a per-poll
rescan, and reuse the session-tree projection rather than recompute a session
list. Each step is its own branch and its own ship-it gate. **Step 4 (added
2026-09-28) is a separate track:** a new *remote-target* collision signal, driven
by a real two-sessions-rebasing-one-PR incident the current signals could not see.

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

## Step 1 — Read the WORK section from the crew-log `work` fold, not the cache (HIGHEST VALUE)

Discovery (subagent 04fb9e78, 2026-09-23, all citations verified) corrected the
premise: there is **no separate parallel store**. The panel reads the work-ledger
JSON **cache** via `work_ledger.read_conductor(sk)` + `list_work_items(sk)`
(`project_panel.py:176,180`), and that cache is itself a materialization of the
same `work` fold — `rebuild_from_projection` (`work_ledger.py:2649`) rewrites the
`it_*.json` files *from* `read_slot_projection(slot, "work", …)`. So the change is
narrow: **the panel stops reading the cache files and reads the fold value
directly** — same record, but the fold is the one authoritative shape, so the
panel and the rebuild agree by construction.

Consequences of the finding:
- **No fold extension needed.** Every panel field maps to an existing fold field
  (`item_id/title/state/status/summary/pr/round` direct; `worker` = the fold's
  `worker_session_key`, then the panel's own leak-gate `wk in project_session_keys`;
  `is_coordinator` = "the `conductor` header has entries"; `coordinator` = the
  header's `slot_key`). The only genuine absence — a human-readable assignee NAME —
  is pre-existing (neither cache nor fold stores it; the panel emits the raw key
  today) and is derived panel-side by joining the session key to the live-slot
  snapshot's `title`/`agent`. Do NOT push slot identity into the fold.
- **Nothing gets deleted.** The cache stays — it is the conductor tools' write /
  resume path (`dashboard/handlers/work_ledger.py`). The panel is simply retiring
  its own two cache reads as a second reader-shape.
- **Do NOT call `work_slots_naming_board` in the panel path** — it is a whole-log
  walk explicitly "for a rebuild, not the per-read fold" (`projection.py:2625`).
  Pass the bound-worker slots the conductor's `bind` entries already name.

Checklist:
- [x] In `api_project_panel`: `read_conductor(sk)` → the fold header from
      `read_slot_projection(sk, "work", also_slots=<bound workers>).value["conductor"]`;
      `is_coordinator` keys off a non-empty header (`entries`), not `read_conductor
      is not None` (`project_panel.py:176-178`).
- [x] `for it in list_work_items(sk)` → iterate `folded.get("items") or ()`; item
      access becomes dict keys (`it["title"]` …) not attributes; the `worker`
      leak-gate reads `it["worker_session_key"]` (`project_panel.py:180-196`).
- [x] Import swap: function-scope `from kiro_crew.crew_log.projection import
      read_slot_projection` (keeps crew-log storage off the boot path; boot-path
      flag-off invariant re-verified green).
- [x] Assignee display name stays a panel-side join (unchanged from today's raw-key behaviour).
- [x] Leave every OTHER cache reader untouched — only the panel's two calls retired.
- [x] Tests: project_panel work-rollup suite updated to stub the fold; 15 green
      (added the `entries==0` bootstrap-window regression test in `0c56e886f`,
      addressing the crew's round-1 MEDIUM).
- [x] Feature-map Coordination row: no change needed — the read moved internally
      (cache→fold inside `project_panel.py`); the row's cited handlers/endpoints
      (`project_panel.py`, `chat_folders.py`, the panel endpoints) are unchanged.
- [x] Ship-it gate; commit. **Paired GO @ `0c56e886f`** (crew GO after MEDIUM fixed +
      panel GO: GPT/Opus PASS). Recorded in `docs/scan-log.md`. **STEP 1 DONE.**


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

## Step 4 — Remote-target collision detection (Signal 3): "another session is already on this PR/branch"

Motivating incident (2026-09-28): two sessions, started independently, both
converged on **rebasing the same remote PR**. Neither shared a worktree or a
local branch on disk, so **no existing signal fired** — the panel showed both as
members and said nothing about the shared *remote target* they were both acting
on. This is a semantic collision on a shared remote object, invisible to the two
filesystem-scoped signals.

**Why the two existing signals miss it (verified in source):**
- **Signal 1 (same-file)** keys on `(project_group_id, repo_id, repo_rel_path)` —
  it fires only when both sessions *edit the same file in a recency window*. Two
  rebases touch different files (or none yet), in different clones. Silent.
- **Signal 2 (same-worktree)** keys on the realpath'd `worktree_root`
  (`worktree_index.py`) — a *shared physical tree*. Separate clones have
  different toplevels by construction, so Signal 2 explicitly does not fire
  ("separate worktrees … fires nothing — the hazard is the shared TREE"). Silent.
- So "two sessions independently operating on remote PR/branch X" has **no key**
  in the current model. It is a genuinely new detection axis, not a variant of
  Steps 2–3.

**Where the target is captured from (investigated 2026-09-28, all in source):**
The capture point already exists and already reads most of what we need. Per
turn, OFF the hot loop, `_flush_collision_writes`
(`chat_runner.py:2453`) calls `derive_repo_context(cwd)`
(`collision_derive.py:derive_repo_context`), which already runs
`git config --get remote.origin.url` and canonicalizes it into `repo_id` via
`canonicalize_remote`. So the **repo identity of the remote is already derived
every turn.** What is NOT captured today is *which ref of that remote this
session is working on*. The clean source for that, at the same off-loop site,
is one more read-only git call from the session's cwd:
- **Checked-out upstream branch:** `git rev-parse --abbrev-ref --symbolic-full-name @{u}`
  (the tracking branch, e.g. `origin/feature/foo`), or the local branch name
  (`git branch --show-current`) as a fallback when no upstream is set. This is
  the cheapest, most reliable "what am I working on" signal and covers the rebase
  case directly (both sessions had the PR's branch checked out).
- **A monitored PR URL** (secondary, richer): a session babysitting a PR via
  `monitor_watch`/`monitor_start` holds the full PR URL — that is an even more
  precise target than the branch, but it lives in monitor state, not git, so it
  is a *second, optional* contributor to the same signal rather than the
  primary source. Primary source = the git ref; PR URL is an enrichment.

**Model (mirrors Signal 2's shape — a live map, not recency-windowed):**
- New leaf `RemoteTargetIndex` (`dashboard/remote_target_index.py`), same shape
  as `WorktreeIndex`: `session -> RemoteTarget(repo_id, ref)` where `repo_id` is
  the already-derived canonicalized origin and `ref` is the canonicalized
  upstream/branch. Collision = a `(repo_id, ref)` held by ≥2 distinct live,
  non-fork-paired sessions. Pure, thread-safe, knows nothing about slots/git/disk.
- Key on `(repo_id, ref)` NOT worktree — that is the whole point: it matches
  ACROSS clones, exactly where Signal 2 deliberately does not.
- Reuse `_all_fork_paired` and the live-session/`is_fork_pair` query contract
  verbatim (a fork on the parent's branch is not the hazard), same as Signals 1/2.

**Wiring (all at the existing off-loop site):**
- [ ] Add `RemoteTargetIndex` leaf + unit tests (pure set math; copy Signal 2's
      test shape).
- [ ] In `derive_repo_context` (or a sibling `derive_remote_target` to keep the
      RepoContext dataclass frozen/minimal), add the one `@{u}`-then-`--show-current`
      read; return `(repo_id, ref)`. Keep it inside the SAME `to_thread` derive so
      it costs one extra git call per flush, not a new spawn path.
- [ ] In `_flush_collision_writes`: `state.remote_targets.set_target(session, …)`
      on every flush (independent of writes, like `set_worktree` — a session that
      rebased without a file-tool write must still register), and evaluate
      `cotenants` for a Signal-3 note.
- [ ] Notification: default-notify like Signal 2 (a live convergence, not a
      recency-windowed maybe), via `collision_notify` with a new `same-remote-target`
      signature — dedupe per `(repo_id, ref, sessions)`, opaque-id body ("2 sessions
      are working on origin/feature/foo"). Add the notify helper alongside
      `sameworktree_should_notify` / `samefile_should_notify`.
- [ ] Panel: add a `remote_target` collisions row to `api_project_panel` +
      `CoordinationPanel.tsx`, alongside the same-file / same-worktree rows the
      panel already renders. This is where the second session *sees* the first is
      already on the PR.
- [ ] Dedupe interaction: if a same-worktree note already covers the same session
      set (two sessions in ONE clone that also share the branch), suppress the
      redundant remote-target note — mirror the Signal-1-⊆-Signal-2 dedupe already
      in the flush.
- [ ] (Optional enrichment, can be its own follow-up) fold a monitored PR URL from
      monitor state into the target when present, for a "…on PR #NNNN" label
      instead of the bare ref.
- [ ] Ship-it gate; commit.

**Open design question to settle before building:** recency. Signal 2 is
NOT windowed (it is "right now, same tree"). A remote target is stickier — a
session can hold a branch checked out for hours between turns. Decide whether
Signal 3 is pure-live (like Signal 2: any two live sessions on the same ref) or
lightly windowed (only flag if BOTH touched the ref recently), to avoid flagging
two long-idle sessions that merely have the same branch checked out and aren't
actively racing. Leaning pure-live-but-last-activity-aware: key on live + a
"last advanced this ref within N min" touch, so a parked session doesn't
generate a standing false collision.

## Deferred / independent (not blocking the convergence)

- [ ] `window.prompt` → inline input in `CoordinationEmptyState` (both UX lanes
      flagged it round-2; non-blocking).
- [ ] Diagnose the in-branch tag-refresh NO-GO (`ac32175a2`, undiagnosed) — needs
      a pod repro; the misdirected `['chat-slots']` invalidation is at best a no-op.

## Order rationale

1 first: it is the largest divergence and the one that most keeps us aligned with
upstream (one work-item source of truth). 2 next: it removes the per-poll rescan
now that 1 has proven we can read from projections. 3 last of the convergence
steps: it depends on understanding both the lane and our own view, and is mostly
a decision plus a read-source swap.

**Step 4 is a NEW capability, not convergence** — it adds a third collision
signal the model never had. It is independent of 1–3 (it extends the collision
index and the off-loop derive site, which Steps 1–3 do not touch) and is driven
by a real incident, so it can be scheduled by value rather than by dependency:
reasonable to take it right after Step 2 (both touch the collision/projection
plumbing and share the "advance at commit / read cheap" discipline), or to pull
it forward if the convergence work stalls. It does NOT depend on Step 3.

The two deferred items are independent and can slot in whenever.
