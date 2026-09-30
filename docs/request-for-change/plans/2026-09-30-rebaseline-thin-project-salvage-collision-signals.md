# Re-baseline onto the Thin Project — Salvage the Collision Signals

> **Active plan.** Written 2026-09-30. Supersedes
> `2026-09-23-coordination-panel-converge-on-crew-log.md`, whose premise (§12.6
> project-group *shared memory*, converged onto the crew-log substrate) was
> already retired by the time it was written. `rfc-crew-projects.md` shipped
> **Revision 2 — "the thin Project"** on 2026-09-18 (origin/main `f21d7603b`,
> `b7b566301`), five days before that plan. Revision 2 walks away from the
> group-shared-memory model this feature is built on. This plan salvages the
> parts that survive.

**Goal:** Keep the parts of the coordination feature that have value
independent of the memory model — the **collision signals** (same-file,
same-worktree, and the proposed remote-target signal) — and re-baseline them
onto the thin Project. Park the group-shared-memory core, which the RFC
retired. The collision signals are model-agnostic and upstreamable: two
sessions racing on the same file, worktree, or remote PR is a real hazard
whether the memory model is "a group of sessions sharing a store" or "one thin
Project a session binds to."

**Branches:**
- Work branch for the salvage: cut fresh per step off `feature/coordination-v2`
  (fork tip `265f86716`, in sync with fork; `origin/main` = `3ecea02fe`).
- Superseded stack preserved: `feature/project-group-shared-memory`
  (`b488d5664`) + backups `backup/pre-rebase-sep30`, `backup/pre-rebase-sep23`.

---

## What the RFC retired, and what it did not

Grounded in `docs/request-for-change/rfc-crew-projects.md` (Revision 2) and the
origin/main tree at `3ecea02fe`:

**Retired — the group-shared-memory core.** Revision 2 replaces "many sessions
share a `project_group_id` and a group memory pool" with **the thin Project**:
a Git repo the gateway materializes, registered under a fenced per-install
registry with a minted `<id>`, that a session **binds to at creation**. Memory
is `project--<id>` — *one store per Project*, provisioned at activation, keyed
to the registration id — not a group pool. The RFC lists "Shared memory across
Projects" as **open question 8**, explicitly deferred. So `group_memory.py`,
`handlers/group_memory.py`, and the `group_memory_*` MCP tools implement a
model the RFC has walked away from. **Park them** (see Step 0); do not build
further on them.

**Survives — the collision signals.** `collision_index.py` (same-file, Signal
1), the same-worktree signal, `collision_notify.py`, and the proposed
remote-target signal (Signal 3) are **model-agnostic in every respect except
the name of one key field.** They depend on nothing from shared memory. They
depend only on: *does a session belong to a coordination scope, and are two
live sessions in the same scope racing on the same file / worktree / remote
target?* The grouping is the only coupling, and it is a single field
(`project_group_id`) threaded through the key dataclasses and notify
signatures.

**Not yet available upstream — the thin-Project binding.** Verified at
`3ecea02fe`: `_ChatSlot` carries `self.project` (a directory path) but **no
`project_id` field.** The RFC's PR A (registry, `project.yaml`, the
`project_id` slot binding, the composer chip) has **not landed** — only
manifest/registry scaffolding is in flight (`apps/builtins/projects/` is the
Task Runner page; `projection/registry.py` is the projection kernel). So the
`project_id` these signals should ultimately key on **does not exist to key on
today.** This shapes the salvage: decouple now, bind when PR A ships.

---

## The salvage strategy: one neutral scope id

The collision signals must not hard-code *what kind of grouping* they key on.
Introduce a neutral **`coordination_scope_id`** — an opaque string that answers
only "which sessions are peers for collision purposes" — and source it from
whatever binding the runtime has:

- **Today:** the existing project-group tag on the slot (unchanged behaviour;
  the signals keep working on the current branch).
- **When PR A lands upstream:** the thin Project's `project_id` from the slot's
  binding. A one-line change at the *source* site, not in the signal code.
- **Fallback:** empty string → the session is unscoped → never indexed
  (already the signals' contract for an untagged session).

The signal code (index dataclasses, notify signatures, panel reads) keys on
`coordination_scope_id` and never names `project_group_id` or `project_id`.
That is what makes the signals survive the RFC shift and land cleanly upstream
whenever the thin-Project binding ships — the collision layer becomes
*independent of the memory-model debate entirely*, which is exactly the RFC's
own design instinct (the collision signals are runtime coordination state, not
bundle data, so they degrade gracefully per-install).

---

## Global Constraints

- Each step lands on its own branch off `feature/coordination-v2`, granular
  test-green commits, staged by explicit path (never `git add .` / `-A`).
- Each step goes through the ship-it gate (ASH + adversarial crew + multimodel
  panel) before it joins the branch. Frontend-only deltas may skip Holmes with
  the recorded rationale.
- The collision-signal code must not name `project_group_id` or `project_id` —
  only `coordination_scope_id`. The binding-to-field mapping lives at ONE
  source site (the per-turn flush that resolves a session's scope), so the
  upstream `project_id` swap is a one-line change there.
- Verify against source at each step; the RFC shift proved that not re-checking
  the design of record is the expensive mistake.

---

## Step 0 — Park the group-memory core, keep the collision leaves

Separate the surviving signals from the retired model cleanly, so nothing built
next drags the group pool along.

- [ ] Inventory the group-memory surface: `group_memory.py`,
      `dashboard/handlers/group_memory.py`, the `group_memory_*` MCP tool
      registrations, and the `delete_group_memory` call in
      `project_store.py:delete_project`.
- [ ] Decide park vs delete. **Park** (leave on the superseded branch, do not
      port to the salvage branch) is the default — the code is gated and green,
      and re-deletion is cheap if the RFC's OQ8 ever reopens shared memory. Do
      not carry the `group_memory_*` tools onto the salvage branch.
- [ ] Confirm the collision leaves have **zero** import edges into
      `group_memory` (`collision_index.py` already imports nothing from it;
      verify `collision_notify.py`, `project_store.py`'s coupling is only the
      GC call, which drops when the group pool is parked).

## Step 1 — Introduce `coordination_scope_id`, decouple the signal leaves (HIGHEST VALUE)

Re-key the collision signals off the neutral scope id. Pure rename-plus-source
change; no behaviour change on the current branch (the scope id is sourced from
today's group tag).

- [ ] Rename `FileKey.project_group_id` → `coordination_scope_id` in
      `collision_index.py`; update `record_edit`/`contested_files` parameters
      and docstrings (the "§12.6 group" language becomes "coordination scope").
- [ ] Rename the notify-signature and body parameters in `collision_notify.py`
      (`_sig`, `samefile_should_notify`, `sameworktree_should_notify`,
      `notification_body`) from `project_group_id` → `coordination_scope_id`.
      The opaque-id-never-name discipline is unchanged and still correct.
- [ ] At the ONE source site (the per-turn collision flush in `chat_runner.py`
      that today reads `slot.project_group_id`), resolve the scope id:
      `slot.project_group_id` today, with a documented single-line seam for
      `slot.project_id` when PR A lands. Everything downstream sees only the
      resolved scope id.
- [ ] Update the panel read (`project_panel.py`) and `slot_projection.py`
      serialization to the neutral field name.
- [ ] Update tests: the collision-index and notify unit suites, which assert on
      the key/signature shape — rename the fixture field, assertions unchanged
      (behaviour is identical).

## Step 2 — Same-worktree signal, re-keyed and verified

The same-worktree signal (the live-filesystem-race case) is the highest-severity
signal and the one most independent of any memory model — a shared physical
worktree is a race no matter what. Confirm it rides the neutral scope id and its
notify path is intact after Step 1.

- [ ] Verify `sameworktree_should_notify` and the worktree index key on the
      resolved scope id, not the old field.
- [ ] Confirm the same-worktree path still notifies by default (no churn
      suppression) and dedupes against same-file (one notification when a
      collision is both).

## Step 3 — Remote-target signal (Signal 3), on the neutral scope (the real incident)

The two-sessions-rebasing-one-PR case (the incident that motivated this signal).
Grounded 2026-09-28: the capture point already derives `repo_id` per turn via
`derive_repo_context`; the missing piece is the checked-out ref
(`git rev-parse --abbrev-ref --symbolic-full-name @{u}`, falling back to
`git branch --show-current`) — one read-only git call at the same off-loop site.

- [ ] Add a `RemoteTargetIndex` leaf keyed on `(coordination_scope_id, repo_id,
      ref)` — matches ACROSS clones, where same-worktree deliberately does not.
- [ ] Capture the ref at the existing per-turn derive site (beside `repo_id`).
- [ ] Settle the recency question (deferred from the Step-4 investigation):
      lean pure-live-but-last-activity-aware, so two long-parked sessions merely
      sharing a branch do not generate a standing false collision.
- [ ] A monitored PR URL (from `monitor_watch` state) is a richer secondary
      source for a "…on PR #NNNN" label; the git ref is the reliable primary.

## Step 4 — Bind to the thin Project when PR A ships upstream (deferred, one-line)

Not actionable until the thin-Project `project_id` slot binding lands on
origin/main. When it does:

- [ ] At the single source seam from Step 1, resolve `coordination_scope_id`
      from `slot.project_id` instead of the group tag.
- [ ] The signal code, the panel, and the tests need **no change** — that is
      the whole point of the neutral scope id.
- [ ] This is the upstreamable form: collision coordination that keys on the
      thin Project, independent of OQ8 (shared memory across Projects).

---

## Order rationale

Step 0 first so nothing built next carries the retired group pool. Step 1 is
the load-bearing decouple — it is what lets every later step and the eventual
upstream binding be a one-line source change rather than a signal rewrite.
Steps 2 and 3 are the two signals with value fully independent of the memory
model (a filesystem race; a remote-PR race), 3 being the one with a real
incident behind it. Step 4 is the upstream landing, gated on PR A.

## What is explicitly dropped

- The group-shared-memory core (`group_memory.py` and its tools) — retired by
  RFC Revision 2, parked on the superseded branch.
- The crew-log convergence plan's Steps 1–3 (fold work items, commit-driven
  projection, conductor-lane reconcile) — those converged the *panel* onto
  main's substrate under the group model. The panel is downstream of the memory
  model the RFC retired; revisit only if the thin Project grows a coordination
  panel of its own.
