# Scan log — feature/project-coordination-phase1

Auditable record of pre-merge gates per the `auto/ship-it` SOP. The user merges;
these entries record the GO/NO-GO decisions and accepted risks against each head SHA.

---

## Phase-1 Signal 2 (same-worktree) + notify + panel — `acb89c387` (branch `feature/project-coordination-collision-worktree-notify`)

Completes the two-signal collision model: the same-worktree strong-warn signal
(`worktree_index.py`), the notify path with noise discipline (`collision_notify.py`),
the per-turn evaluate-and-notify wiring (`chat_runner.py` flush), and the read-only
`GET /api/projects/{id}/panel` (`project_panel.py`). Diff base is the Signal-1 tip `f08dfbc4d`.

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 43 collision/panel tests + 776 dashboard neighbors green |
| ASH (`03f12423`, 10 scanners) | GO for diff | 0 actionable on changed files (bandit note-level subprocess advisories only) |
| Adversarial crew (5 axes, on `2ec25efe9`) | GO after fixes | Security Blocker (panel authz gate) + High (id oracle); Correctness High (per-tree dedupe); AI (dead `forget`/`drop_session` removed, `_all_fork_paired` deduped); Tests (stranger-breaks-fork + both-signals-one-note + panel same-worktree/cross-project). All applied + re-verified. |
| Multimodel panel (`acb89c387`) | GO | GPT PASS, Opus PASS, Design PASS, First-Principles CONCERNS (per-flush-vs-eager scoping = accepted taste) |

**Paired record:** `acb89c387 | crew: GO (all 5 axes; Security/Correctness/AI/Tests findings applied) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`
(Crew ran on `2ec25efe9`; findings fixed in the commits since. Panel GO is on the
final `acb89c387`.)

**Panel loop (eval corpus — seven GPT rounds, honestly split):** rounds 1–5 were
REAL delivery-surface defects, each fixed at root: panel served any project's
name/session-titles with no authz → app-ownership gate + uniform 404 (no id
oracle); the per-turn dedupe bool wrongly suppressed a cross-worktree same-file
collision → per-tree dedupe (`fsessions <= members`); `NotifyOnce` grew unbounded
→ liveness prune (drop a signature once ANY participant dies); Signal 2 evaluated
only on write turns → evaluate on every flush; the panel same-worktree flag leaked
another project's session ids → own-project filter; a retagged session leaked into
its old project's same-file flag → scope to currently-tagged sessions; same
repo_rel_path in two repos deduped to one note → fold `repo_id` into the signature.
Rounds 6–7 were DOC-HONESTY: docstrings claimed a "standing / eager re-derived on
every slot.project commit" view and a "last activity" field the per-flush code does
not provide; fixed by aligning all docstrings/comments to the per-flush,
eventually-consistent reality (the whole class swept at once, which broke the loop).
Opus PASSed every round; the code was correct by round 5.

**Accepted follow-ups (do not block):**
1. Eager `worktree_root` re-derive on every confirmed `slot.project` commit (the
   8 mutation sites), replacing the per-flush approximation — closes the
   "two just-opened sessions don't flag until each flushes" window. Documented
   in the code as the Phase-1 approximation.
2. Notify `bus.push` is same-failure-domain (Design CONCERNS): wrapped in
   try/except with the panel's collision flags as the independent advisory
   backstop, so a lost push is not silent data loss — but no durable-write
   fallback. Revisit if the notify path becomes load-bearing.
3. Same-file churn suppression: a 2-session collision that grows to 3 notified at
   the 2-set then goes panel-only (intended; the panel still shows it).

---
---

## Phase-1 Signal 1 — same-file collision detection — `1698578cf` (branch `feature/project-coordination-collision-samefile`)

Same-file merge-risk signal: ≥2 distinct LIVE sessions tagged into one project
editing the same repo-relative file within 30 min. New
`src/kiro_crew/dashboard/collision_index.py` (in-memory index) +
`collision_derive.py` (off-loop git derivation), wired into the turn hot path
(`chat_runner.py`), `DashboardState.collisions` (`state.py`), turn-start reset
(`chat_handlers.py`). Diff base is the tagging-API tip `222ce4a86`.

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 35 collision tests + 776 dashboard neighbors green |
| ASH (`f982f266`, dashboard dir, 10 scanners) | GO for diff | 0 actionable on changed files; only bandit `note`-level subprocess advisories (B404/B603/B607 on intentional git calls, B112 on the deliberate error-swallow) |
| Adversarial crew (5 axes, on `43b816f91`) | GO after fixes | No Blocker. Security-Medium (per-path git-spawn storm) → memoize repo-lookup once per cwd + per-flush path cap. AI-Medium → drop write-only `worktree_root`. Tests-High → cap/sensitive-skip/git-failure/fork tests. All applied + re-verified. |
| Multimodel panel (`1698578cf`) | GO | GPT PASS, Opus PASS, First-Principles + Design CONCERNS (unwired read-surface / ships one commit ahead of the Signal-2 consumer — the deliberate two-commit split) |

**Paired record:** `1698578cf | crew: GO (all 5 axes; Security/AI/Tests findings applied on the commits after 43b816f91, re-verified) | panel: GO (GPT/Opus PASS, First-Principles/Design CONCERNS = scoping/taste)`
(Crew ran on `43b816f91`; every finding was fixed in the commits since. Panel GO
is on the final `1698578cf`, which the crew fixes are folded into.)

**Panel loop (eval corpus — panel-caught data-loss/leak class):** the panel
BLOCKed three successive SHAs, each a distinct real defect in my own hot-path
code, none a false positive: `aa21f4163` — a newest-N-rows cap could evict a
distinct session's row (false-negative collision) → fixed at root by storing
ONE row per session per key (bounded by distinct-session-count, cap removed);
same SHA — a `state.py` comment claimed a wired panel/notify consumer that does
not exist → corrected; `79c4d2541` — `record_edit` pruned only the touched key,
leaking stale keys for the process lifetime → fixed by calling `index.prune()`
(all-key sweep) every turn-exit, off-loop. `1698578cf` cleared it.

**Accepted follow-ups (do not block; land with Signal 2 / the panel consumer):**
1. The index read surface (`contested_files`, `prune` caller for reads,
   `distinct_session_count`) ships one commit ahead of its panel/notify consumer
   — a deliberate two-commit split (Signal 1 = detect+index; Signal 2 + delivery
   = next commit). First-Principles/Design flag this as scoping; the consumer
   lands next.
2. `is_fork_pair` symmetry is a documented contract; the real predicate (built
   from `forked_from`) lands with the panel/notify consumer that supplies it.

---

## Phase-1 Step 4 — project-group tagging API — `0c1a3d3d6` (branch `feature/project-coordination-tagging-api`)

New `POST /api/chat/slots/{slot}/project-group` (`api_chat_slot_project_group` in
`src/kiro_crew/dashboard/chat_folders.py`) — the validated create-or-attach-or-untag
interface and first caller of `ProjectStore`, wired as a shared `DashboardState.projects`
instance. Diff base is the store branch tip `b04ccbad4` (store + field plumb gated GO
separately below).

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 16 endpoint tests green (`./.venv/bin/pytest test/test_chat_slot_project_group.py`); neighbors + body-guard ratchet green |
| ASH (`888404a5`, dashboard dir, 10 scanners) | GO for diff | 279 dir-wide findings, **0 on any changed file** (`chat_folders.py`/`state.py`/`routes/sessions.py`); the 4 criticals are pre-existing `token_*.py` |
| Adversarial crew (5 axes, on `fb7eca370`) | GO after fix | Correctness HIGH (create-then-fail orphan record) — FIXED (`7d89a3c67`: create inside the lock + compensating delete) and re-verified with tests. Security/Docs/AI/Tests GO. |
| Multimodel panel (`479804636`) | GO | GPT PASS, Opus PASS, First-Principles PASS, Design CONCERNS (watch, non-blocking) |

**Paired record:** `0c1a3d3d6 | crew: GO (all 5 axes; Correctness HIGH orphan-record fixed on 7d89a3c67, re-verified) | panel: GO on 479804636 (GPT/Opus/First-Principles PASS, Design CONCERNS=watch)`
(Crew ran on `fb7eca370`; its one HIGH was fixed in the commits since. Panel GO is on
`479804636`; `0c1a3d3d6` adds only a tests-only mock-hardening delta — handler bytes
identical — so the panel GO stands without a re-gate, per the ship-it tests-only exemption.)

**Panel loop (kept for the eval corpus — panel-caught data-loss class):** the panel
BLOCKed three successive SHAs, each a distinct instance of one class — a
"present-but-not-a-real-target silently untags an existing project" data-loss path:
`fb7eca370`→ N/A (crew round); `7d89a3c67` GPT BLOCK (blank/whitespace `name` → silent
untag) + Design BLOCK (duplicate shadowed test); `52dbc9458` GPT BLOCK (present JSON
`null` name/id → silent untag). Root fix: untag is the key-ABSENT signal ONLY; a present
key must carry a non-empty string, else 400. `479804636` cleared it.

**Accepted follow-ups (do not block; land with later Phase-1/2 work):**
1. Per-caller rate-limit on the create branch (crew Security Nit) — cap-bounded at
   `_MAX_PROJECTS`=500 → 503 today; add `allow_create(...)` for parity with folder-create.
2. Dangling-tag reconciliation (Design watch): the attach `get_project` pre-check is
   advisory not transactional; a project deleted between check and commit leaves a dangling
   tag. Tolerated by the store's delete-always/dangling-id-is-a-reader-concern contract, but
   there is no GC/scrub. Add a lazy-clear or periodic reconcile, and assert the reader
   tolerance as a store class-invariant, when the reader/notify path lands.
3. 48-bit id mint × idempotent-by-id create (crew Correctness Nit): a `uuid4().hex[:12]`
   collision would silently attach to a pre-existing different project; negligible at the
   500 cap, retry-on-collision if ever tightened.

---

## SHA `c255a4a1a` (field plumb + round-trip tests) — GATE: GO

**Change:** Phase-1 `project_group_id` slot field plumb (`85b2ebb26`) + round-trip
tests (`c255a4a1a`). New persisted grouping tag on `_ChatSlot`, distinct from the
`project` cwd path; plumbed through `__slots__`/init, `SLOT_OWNED_META_KEYS`, both
metadata writers, all four rehydrate sites, fork copy, and workspace-switch
clear+rollback. 7 tests, green under `.venv` pytest.

| Gate | Verdict | Notes |
|---|---|---|
| Build / tests | GO | 6 files compile; `test_project_group_id.py` 7 passed under `.venv` pytest |
| Multimodel panel (`85b2ebb26`, code unchanged since) | GO | GPT PASS · Opus PASS · Design PASS · First-Principles CONCERNS (taste/subtraction lane — terminating condition, not a harm path) |
| Adversarial crew — Correctness | GO | Round-trip verified all 4 read sites (identical one-line mirrors); `__slots__`/owned-keys/rollback/fork all correct |
| Adversarial crew — Security/Docs-honesty/AI-necessity | GO | Zero downstream consumers; `str()` coercion more defensive than sibling `project`; every commit-msg claim verified; no LLM lines |
| Adversarial crew — Tests (initial `85b2ebb26`) | NO-GO → fixed | HIGH: no round-trip test |
| Adversarial crew — Tests (re-review `c255a4a1a`) | GO | HIGH closed with real assertions against production code |

**Paired record:** `c255a4a1a | crew: GO (all axes; Tests HIGH fixed by round-trip tests) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`

**Security / ASH / Holmes:** additive Python field plumb, no IaC, no new deps
(lock file unchanged). ASH/Holmes not separately run this round — the change adds
no attack surface (a persisted string with zero consumers) and the crew Security
axis covered injection/trust (hostile metadata line coerced via `str()`, field
inert). Recorded honestly: ASH/Holmes NOT RUN this round; crew Security = GO.

**Adjudicated (not fixed, by design):**
- Correctness "High" `slack/handler.py:930` — reads `project` into a module-level
  `_thread_projects` cache, not onto a `_ChatSlot`; no slot to tag, so it is out
  of the slot-field-plumb scope by construction. Deliberate non-site.
- Panel First-Principles CONCERNS — subtraction/taste lane; the documented
  terminating condition for the fix-all loop.

**Accepted follow-ups (do not block this checkpoint):**
1. Fork test is tautological (asserts the copy line directly) — strengthen to
   drive `api_chat_slot_fork` so a dropped `chat_fork.py` copy line fails.
2. Workspace-switch rollback uses a value guard (`if not slot.project_group_id`)
   where `project`/`workspace` use `_CommitToken` identity — only matters once a
   concurrent tagging writer exists (Phase 2). Revisit when the tagging API lands.

**Scope note:** this SHA is the FIELD PLUMB only — not full Phase 1. The
`projects.json` store, tagging API, collision signals (same-file + same-worktree),
notify, and panel are later commits and will gate separately.

---

## Phase-1 Step 2 — `projects.json` store (`ProjectStore`) — `6f5d6a985`

New `src/kiro_crew/dashboard/project_store.py` + `test/test_project_store.py`.
A machine-owned durable record table (`{id, name, repos[]}`) a session's
`project_group_id` points at. Only writer is `ProjectStore` (json.dumps + atomic
write); mirrors the crons.json durability pattern (flock, blake2b
`_sync_for_write` RMW, atomic tmp→rename) with none of the scheduler machinery.

**Design correction that ended a 6-round GPT data-loss loop (user decision):**
the file is MACHINE-OWNED — we do not accept hand-authored records. So the store
validates its OWN invariants (a failed write must not lose data; a concurrent
process must not be clobbered; our own file failing to read/parse is corruption,
not user input → fail loud) and does NOT defensively coerce foreign input. The
prior rounds' per-field coercion (scalar `repos`→`[]`, invented `"None"` ids,
salvaged half-parsed rows) was defending an input class that cannot occur, and
was deleted. `from_dict` enforces the EXACT shape and raises `ProjectStoreCorrupt`
on any deviation (missing/wrong-typed field OR unknown key).

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | 28 store + 7 field tests green (`./.venv/bin/pytest`) |
| Multimodel panel (`6f5d6a985`) | GO | GPT PASS, Opus PASS, Design PASS; First-Principles CONCERNS (unwired-store/Step-4-split = taste) |
| Adversarial crew — Correctness | GO | No Blocker/High/Medium; idempotency, TOCTOU-close, digest-ordering, transactional-rollback all hold, each pinned by a non-tautological test |
| Adversarial crew — Security | GO | Paths derive from `config_dir()`/`base_dir`, never from id/name; json-only deser; fd lifecycle clean; caps enforced under lock |
| Adversarial crew — Tests | GO | Contract covered; strengthened cross-instance TOCTOU + cap + observe-empty per crew Medium/Nits |
| Adversarial crew — Docs | GO | Docstrings match code; no stale coerce/degrade phrasing |
| Adversarial crew — AI/over-engineering | GO | M1 (from_dict unknown-key contradiction) fixed by enforcing exact shape; M2 double-raise collapsed |

**Paired record:** `6f5d6a985 | crew: GO (all 5 axes; M1 exact-shape fix + Tests-Medium cross-instance TOCTOU test applied) | panel: GO (GPT/Opus/Design PASS, First-Principles CONCERNS=taste)`
(Final branch SHA `99786eec8` = `6f5d6a985` + this scan-log entry, docs-only; the
gated code bytes are identical, so both verdicts stand without a re-gate.)

**Security / ASH / Holmes:** additive Python + JSON persistence, no IaC, no new
deps. Crew Security axis = GO (no injection/traversal/deser/fd-leak on changed
lines). **ASH RUN** (`9211b8df`, dashboard dir, MEDIUM threshold, 10 scanners):
279 findings dir-wide but **ZERO reference `project_store.py`** — the 4 criticals
(detect-secrets) are in pre-existing `token_secret.py`/`token_auth.py`, and the
bandit medium/low are in other dashboard files. This diff adds **0 actionable
findings**. Gate: PASS for the diff.

**Loop history (kept for the eval corpus — panel-caught / crew-caught):** the
panel BLOCKed 6 successive SHAs, each a distinct GPT data-loss finding on the
store's defensive parsing (`{"projects":null}` → `repos:5` → trailing corrupt
byte → failed-save cache divergence → `{"id":null}`→`"None"` → digest poison-on-
raise). Opus PASSed throughout; First-Principles CONCERNS-then-BLOCK on the
unwired store the whole time. Root cause was a wrong unit-of-review + a wrong
posture (defending foreign input into a machine-owned file), fixed by the
machine-owned reframing, NOT by a 7th patch. The final digest-poison finding
(`18ab7c0c3`) was a genuine ordering bug in our own code and was fixed.

**Accepted follow-ups (do not block; land with the Step-4 caller):**
1. `_MAX_REPOS` / per-repo-length cap on `observe_repo` (crew Security Nit) —
   mirror the `_MAX_PROJECTS`/`_MAX_NAME_CHARS` discipline once the tagging API
   drives it.
2. Shared-dir lock-file permission caveat — only relevant if a future caller
   points `base_dir` at a world-writable dir; `config_dir()` is user-owned.

**Scope note:** this SHA is the STORE only. Its validated creation interface
(the tagging API — the store's only intended caller), collision signals, notify,
and panel are later commits and gate separately. The store is intentionally
UNWIRED this commit (acknowledged Step-2/Step-4 split).

---

## feature/project-coordination-tagging-ui — interactive create-or-pick tagging UI

**Head SHA:** `386dc470b` (panel + adversarial reviewed on `569db40f8`; `386dc470b`
is a comment-only JSDoc delta from it — the non-behavioral skip clause applies).
**Base:** `c0f9f7bc9` (Signal-2 tip — the UI depends on the full Phase-1 stack,
so it branches off that tip, not origin/main).

**What it ships:** GET `/api/projects/coordination` (`api_projects_coordination_list`)
returning `{projects:[{id,name}]}` from `ProjectStore.list_projects()`, app-ownership
gated by mirroring the sibling `api_project_panel` predicate; `project_group_id` added
to `slot_projection.py`; frontend `ProjectTagSubmenu.tsx` (create/pick/untag) + the
`listCoordinationProjects`/`setSlotProjectGroup` client pair + `project_group_id` on
the `ChatSlot` type, wired into the shared `SessionActionsMenu`.

**Build/tests:** frontend `tsc -b` clean; backend `test_project_coordination_list.py`
(8 tests incl. app-ownership non-leak, empty-owned, untag-drop, static-vs-dynamic
route resolution + registrar-order invariant), panel/store suites green.

**ASH:** RUN via MCP (`fe1477d3`, dashboard dir, MEDIUM, 7 scanners incl.
detect-secrets/checkov) — **0 findings**. Committed lock files present
(`website/package-lock.json`; no new deps).

**Holmes:** RUN (`5b51a99a`, default baseline, 8 changed files) — **0 findings**.

**Adversarial pre-merge (crew, on `43726c8f5`):** Security GO, Correctness GO,
Docs-honesty GO, AI-necessity GO; **Tests NO-GO** → all findings fixed on
`569db40f8`: route-resolution test [High], empty-owned + untag-drop tests [Med],
`repos` field subtracted from the list payload [Nit, two axes], two comment
softenings [Nit].

**Multi-model panel (on `569db40f8`, report `docs/scan-tagui-panel.md`):** GATE
**GO** — GPT 5.6 PASS, Opus 4.8 PASS, First-Principles PASS; Design + UX CONCERNS.
The Design/Opus JSDoc-drift CONCERN (`listCoordinationProjects` still said
`repos`) fixed on `386dc470b`. UX CONCERN = the acknowledged `prompt()`
placeholder (Phase-4-first, known tradeoff) — see follow-up.

**Both high-bar lanes (GPT + Opus) PASS on the candidate SHA → GO.**

**Accepted follow-up (do not block):** replace the `handleCreate` `prompt()` with
a proper create modal (accessible, validated) — the Phase-4 UI polish the
component comment flags; carry an owner/ticket before wider audience.

---

## tagging UI — post-eval fixes (list-refresh regression + dedup-by-name)

**Head SHA:** `3bf045070`. **Base:** `386dc470b` (the gated tagging-UI head).
Found by hands-on local use of the dev instance before these landed.

**What it ships:**
1. Frontend `SessionActionsMenu.tsx` — removed `staleTime` from the
   `coordination-projects` query. A non-zero staleTime served the stale
   pre-create (empty) list after a create (menu closes → `invalidateQueries`
   has no mounted observer), the "only ever shows New project" bug. Default 0
   refetches on every menu open.
2. Backend `chat_folders.py` create-by-name path — dedup by attaching to an
   existing same-name project (inside the txn lock) instead of minting a
   duplicate. Two sessions naming the same project land in one group.

**Build/tests:** frontend `tsc` clean; tag suite 18 tests (incl. dedup-attaches
and attach-409-preserves-shared), list/panel/store suites green.

**ASH:** RUN via MCP (`6bede233`, dashboard dir, MEDIUM, 8 scanners incl.
bandit/detect-secrets/checkov) — **0 findings**.

**Holmes:** RUN (`07563370`, default baseline, 2 changed files) — **0 findings**.

**Adversarial re-review (crew, 3-axis on the delta):** Docs/AI-necessity GO;
Correctness + Security converged on ONE **Blocker** — the save-failure rollback
`if project_name: delete_project(project_group_id)` deleted a PRE-EXISTING shared
project on the attach-by-name path (orphaning other sessions' tags). Fixed on
`6489d6bf3`: rollback now deletes only a request-local `created_record_id`
(minted-this-request), never an attached-to id; regression test added.

**Multi-model panel:** ran twice.
- On `6489d6bf3` (report `docs/scan-tagui-fixes-panel.md`): **NO-GO** — GPT 5.6
  BLOCKed attach-by-name as a cross-tenant oracle. Opus + First-Principles PASS.
- Adjudicated a **model-scoped false positive**: coordination is single-user
  (Decisions-of-record row 10) — no project "the user cannot see", attach-by-id
  already applies no per-project gate, so attach-by-name is the feature. Threat
  model documented at the dedup site + design §9. Re-run on `3bf045070` with the
  model stated (report `docs/scan-tagui-final-panel.md`): **GO** — GPT PASS,
  Opus PASS, First-Principles PASS; Design + UX CONCERNS (the accepted `prompt()`
  follow-up + JSDoc).

**Both high-bar lanes (GPT + Opus) PASS on the candidate SHA → GO.**


---

## P2.2 — work-ledger progress rollup in the project panel

**Head SHA:** `968bb569a`. **Base:** `1281db62e` (the tagging-UI branch tip — P2.2
builds on the full Phase-1 + tagging-UI stack, which is why it branches off that
tip rather than origin/main; it is a distinct feature gated on its own delta).

**What it ships:** `GET /api/projects/{id}/panel` now returns a `work` rollup
DERIVED from group members' conductor ledgers (design §12, decisions Q1/Q2 — no
stored coupling): each live tagged session with a readable conductor ledger is
flagged `is_coordinator`, and its work items surface with plan+progress fields
(title/state/status/summary/pr/round) plus the in-project worker. Pure gateway
derivation in the existing off-loop scan.

**Build/tests:** frontend unchanged (backend-only payload); `test_project_panel.py`
14 tests incl. the 6 rollup cases (flag+items, channel-born coordinator, zero-items
conductor, multiple conductors, no-ledger empty, cross-project worker non-leak);
77+ green across the coordination suite.

**ASH:** RUN via MCP (`f727fad2`, dashboard dir, MEDIUM, 9 scanners incl.
bandit/detect-secrets/checkov) — **0 findings**. (Re-run not needed after the
key-logic fix: delta was logic + tests, no new imports/surface.)

**Holmes:** RUN (`90e21234`, default baseline) — **0 findings**.

**Adversarial pre-merge (crew, 4-axis on the P2.2 diff):** Security GO,
Docs/AI-necessity GO; Tests NO-GO (2 Medium coverage gaps: zero-items conductor,
multiple conductors) AND **Correctness NO-GO with a Blocker (B1) + High (H1)** —
the rollup looked the ledger up by the RAW slot key (`s.key`), but the conductor
tools write it under the EFFECTIVE session key (the on-wire `KIROCREW_SESSION_KEY`:
`chat_runner.py:6149` sets the turn key = `effective_session_key(slot)`,
`acp/client.py:5175` injects it). `effective_session_key` prefixes `dashboard:`
(or is a channel slot's `slack:<ts>`), so `slot.key` never matches — the rollup was
silently DEAD for every dashboard coordinator, and worker ids were always nulled.
Two axes DISAGREED (Tests mutation-tested and concluded raw was right, because its
stubs were also keyed raw); adjudicated by tracing to the real writer in source —
effective is correct.

**Fix (`968bb569a`):** key `read_conductor`/`list_work_items` and the worker
non-leak set off the effective session id; drop `raw_key` entirely. Added the
channel-born-coordinator test (the case the raw lookup missed) + zero-items +
multi-conductor tests.

**Multi-model panel (on `968bb569a`, report `docs/scan-p22-panel.md`):** GATE
**GO** — GPT 5.6 PASS, Opus 4.8 PASS, First-Principles PASS, Design PASS (UX
skipped, out of scope for a backend change). No CONCERNS.

**Both high-bar lanes (GPT + Opus) PASS on the candidate SHA → GO.** The adversarial
Blocker was a real silent-dead-feature bug caught pre-merge and fixed.


---

## P2.3 — create_session tags a worker into a project group at birth

**Head SHA:** `cdb6deb7e`. **Base:** `ff46a59c5` (the P2.2 tip — P2.3 builds on the
stack; distinct feature, gated on its own delta).

**What it ships (design §12.3, the coordinator actuator's one gateway primitive):**
`session_control.create_session` gains a `project_group_id` param that tags a new
worker session into an EXISTING project group at birth, so a coordinator's
dispatched worker is a member the P2.2 panel rollup surfaces. Attach-only,
validated against `ProjectStore` (unknown id → 404 `project_group_not_found`,
mirroring the tagging API's attach-by-id path; never create-by-name). Plumbed
through the HTTP handler and the `session_create` MCP tool (+ bounded FieldSpec).
The plan/dispatch/evaluate loop stays a skill (decision Q3).

**Latent fix folded in:** the empty-window merge in `chat_persistence.py` now
writes `project_group_id` — a `SLOT_OWNED_META` key it omitted since Phase 1, so a
tagged idle session lost its tag on restart via that path. Caught by the
`SLOT_OWNED` drift-guard test.

**Build/tests:** 195 coordination-suite tests green (incl. 3 new create_session
cases + the drift-guard); compile clean.

**ASH:** RUN via MCP (`edb4eb8f`, dashboard dir, MEDIUM, 10 scanners) — **0
findings attributable to the diff**. Dir-wide totals (4 crit detect-secrets, 13
med / 266 low bandit) are ALL pre-existing: the only two findings referencing a
changed file are bandit "Try/Except/Continue" advisories in `session_control.py`
on lines the diff did not add; the detect-secrets criticals are in
`token_auth.py`/`token_secret.py` (unchanged). Verified against the diff.

**Holmes:** RUN (`2619dd41`, default baseline, 5 files) — **2 findings, both
pre-existing and NOT attributable to the diff**: a `dangerous-subprocess-use`
[high] + B603 [low] on `validation.py:533`, an existing `subprocess.run` with a
`# noqa: S603 — fixed argv` suppression and rationale; the diff's `validation.py`
hunk adds only a FieldSpec (no subprocess). 0 for the change.

**Adversarial pre-merge crew: NOT RUN.** Two full 4-axis waves (8 axis-spawns)
**Adversarial pre-merge crew (4-axis):** two initial waves crashed on runtime
`AcpProcessDied` (spawn-path infra, ample resources) and produced no verdicts; a
third wave (after the runtime recovered) COMPLETED 4/4. Result: **GO**, no
Blocker/High. Findings, all fixed on `b9f2d3e25`:
- Security GO; Correctness GO; AI-necessity GO (attach-only verified safe, the
  dangling-tag tolerance confirmed real against `project_panel`, both HTTP+MCP
  paths necessary).
- **[Medium, Docs-honesty]** the original commit narrative claimed the merge
  "omitted project_group_id since Phase 1 → tagged session lost its tag on
  restart." The crew EMPIRICALLY DISPROVED this: the parent already wrote it
  conditionally in `_fresh_fields` + the full save, so a tagged session's tag
  survived. The real (narrower) defect: a non-excluded SLOT_OWNED key written
  conditionally failed the drift-guard (which saves an UNTAGGED newborn). Fix:
  promote to a clearable write (correct); narrative corrected in the fix commit.
- **[Medium, Correctness]** the promotion left the pre-existing conditional
  write of the same key in `_fresh_fields` as dead code. Removed.
- **[Medium, Tests]** the MCP `session_create` project_group_id plumb (the
  coordinator entry point) was untested. Added `TestSessionCreateProjectGroup`
  (forwards-into-body, omitted-when-absent, schema-bounds-overlength).
- **[Nit]** `projects is None` branch untested — production-unreachable
  (`__init__` always sets a store) and fails safe; left.

**Multi-model panel (on `cdb6deb7e`, report `docs/scan-p23-panel.md`):** GATE
**GO** — GPT 5.6 PASS, Opus 4.8 PASS, First-Principles PASS, Design PASS (UX
skipped, backend-only). No CONCERNS.

**Paired verdict @ crew-reviewed `cdb6deb7e` → fixes on `b9f2d3e25`:** crew GO
(3 Medium fixed) | panel GO (GPT/Opus/FP/Design PASS). The `b9f2d3e25` delta over
the reviewed SHA is a dead-line removal + comment + 3 tests (non-behavioral —
gate re-run not required; 365 coordination-suite tests incl. the drift-guard
green). Both review gates GO. Final head: `b9f2d3e25`.


---

## §12.6 Project-group shared memory (store + injection tier + MCP tools + GC) — `feature/project-group-shared-memory` (rebased onto current `origin/main`)

The Phase-3 §12.6 feature on top of the (rebased) peer-coordination stack:
`GroupMemoryStore` (per-group markdown blob under `data_home()/group-memory/<id>`,
path-traversal-guarded, atomic writes under a per-group advisory flock, locked
append with a total-blob cap, `read()` swallows only FileNotFound/NotADir and
propagates other OSError so the HTTP read handler 503s and the tier self-defers);
the injection
tier in `context.py` (between global memory and session lessons, window-scaled
`caps.group`, self-defers on no-tag/no-memory/malformed-id/unreadable); two MCP
tools on `kirocrew-dashboard` (`group_memory_read` = any tagged member,
`group_memory_write` = coordinator-only, group resolved server-side from the caller
slot, never a request arg); and GC hooked into `ProjectStore.delete_project`.

| Gate | Verdict | Notes |
|---|---|---|
| Build + tests | GO | store/tier/routes + coordination suites green (incl. new append/blob-cap/read-OSError/precedence + GC tests) |
| ASH (`f7ae4f72`, 8 scanners: bandit/opengrep SAST, grype/npm-audit/syft SCA, checkov/cdk-nag/cfn-nag) | GO for diff | 0 findings attributable to the diff; one grype MEDIUM (`pytest 8.3.4` GHSA-6w46-j5rx-g56g) is PRE-EXISTING on main in `aws_control/.../requirements-dev.txt`, not in our diff |
| Holmes (`2926d59c`, default baseline) | GO for diff | 1 MEDIUM `regex_dos` in `context.py` — PRE-EXISTING (our diff added no `re.compile`; only the group-memory import + caps field + tier) |
| Adversarial crew (6 axes, on `1fb07eb19`←`57febe74e`) | GO after fixes | NO-GO round: Correctness H1 (unreadable-blob tier crash) + Data-integrity H (deterministic `.tmp` name / lock-free append lost-update) + M1 (unbounded append blob) + Security M (coordinator-class-vs-group wording) + Nits. ALL fixed: `read()` swallows only FileNotFound/NotADir and propagates other OSError (HTTP read 503s, tier self-defers, append relies on the propagate so it never overwrites an unreadable blob); writes use `atomic_write` under a per-group flock; locked append with `GROUP_MEMORY_BLOB_MAX` refusal; wording/typo/marker-cap corrected. Security/AI-necessity axes GO (deterministic; unforgeable server-side group id). Tests axis GO-with-followups (precedence test now asserts both halves). |
| Multimodel panel (`docs/scan-group-memory-1fb07eb.md`) | GO after fixes | NO-GO round: GPT 3 BLOCKs — (1) append over `read()` could overwrite an unreadable blob = data loss; (2) sink rationale claimed the injection path redacts when it does not; (3) docstring claimed a human dashboard write path that does not exist. Opus PASS. All 3 fixed: append reads directly under the lock (only FileNotFound=empty, other OSError propagates); sink rationale corrected to name the HTTP-read boundary (injection is unredacted, same as sibling memory tiers); docstring corrected to coordinator-only MCP. Design/UX CONCERNS = `prompt()` (documented Phase-4 follow-up) + blob-cap message (reworded to user vocabulary). |

**First-Principles / GPT / Design convergent finding — FIXED (subtraction).**
All three lanes flagged `ProjectStore.observe_repo` + the panel `repos` field +
`CollisionIndex.distinct_session_count` as dead-code-behind-a-completeness-claim
(0 production callers; panel shipped `[]`). Removed all three, plus the
"auto-derived rollup" module-docstring paragraph and the stale `_save` nested-
rollback note. The `repos` removal changes the on-disk `projects.json` shape, so
`Project.from_dict` was given a BACK-COMPAT path (a legacy `repos` list key is
tolerated and dropped on load; any OTHER extra key is still corruption) — Design's
"schema one-way door" Watch, closed by loosening `from_dict` BEFORE removing the
field, exactly as it prescribed. Tests updated: legacy-repos-loads + unknown-key-
still-corrupt, panel/list payloads assert no `repos`, collision one-row test
rewritten on `contested_files`.

**Design/UX CONCERNS (accepted follow-ups, not this gate):** `prompt()` for
project creation (documented Phase-4 inline-input replacement); the `bus.push`
notify sharing the flush failure domain with the panel flags (accepted Phase-1
risk, revisit trigger = coordinator dispatch threshold). The earlier "Open the
project panel" body text + dead `/projects` deep link were REMOVED (the notify is
now self-contained), since no panel UI page exists this phase.


**Panel loop (6 rounds) and the stop-criterion GO.** The multimodel panel ran 6
times as fixes landed. Opus PASSed EVERY round (no code defect ever grounded).
The other lanes ended non-blocking: First-Principles BLOCK→CONCERNS (its dead-code
subtraction was done), Design BLOCK→PASS/CONCERNS (its schema-migration Watch
closed by the `from_dict` back-compat), UX CONCERNS (the `prompt()` Phase-4
follow-up). GPT stayed BLOCK across all 6, but its findings descended from real
harm-paths (round 1-2: overwrite-on-unreadable data loss, redaction-rationale
mismatch, false human-write-path claim — ALL FIXED) into a documentation-
consistency tail (rounds 4-6: a docstring clause, then the scan-log history line,
then two comments made stale BY fixing the prior line, then the GC "never raises"
comment made stale BY the GC-honesty fix). Each late GPT item was fixed as it
arose (append=read() simplification, notify/panel docstrings, GC honest return +
its comments). Round-6's items were the doc-wake of round-5's own behavior change
and are corrected in the final SHA.

**Stop criterion invoked (per auto/ship-it):** GO once code is clean (Opus PASS
every round; 622 tests green across the §12.6 + coordination + ratchet surface)
AND the remaining lane's findings are documentation-consistency / taste, not a
harm path. Every substantive finding from BOTH gates was fixed at root: tier
crash on unreadable blob, write atomicity + concurrent-append lost-update
(flock + atomic_write), unbounded append (blob cap), redaction-rationale honesty,
false human-write claim, read-503 status honesty, dead-code subtraction (with
schema back-compat), GC honest return. The GPT doc-consistency loop is the
terminating condition, not a defect.

**Paired verdict @ final `6a48cc7d5` (+ the round-6 comment fix):**
crew NO-GO→GO (all Correctness/Data-integrity/Security/Tests findings fixed) |
panel: Opus PASS, First-Principles/Design/UX CONCERNS (non-blocking, documented
follow-ups), GPT doc-consistency BLOCK adjudicated as the stop-criterion
terminating condition (all its substantive findings fixed; remaining items are
docstring/scan-log text, corrected). Both gates addressed. The user merges.

---

## Coordination side-panel UI — §12.6 frontend (2026-09-16)

Scope: `67a0e7c48..c49f22f8b` (12 files, frontend-only — React/TS + i18n JSON +
a Playwright capture harness; no Python, no IaC, no dependency/lockfile changes).
Adds the Coordination Chat side-panel view rendering `GET /api/projects/{id}/panel`
(header · members · work rollup · collisions), the `getProjectPanel` API method +
`ProjectPanel` types, a `coordination` ViewKind wired through the tab tables,
`project_group_id` threaded from the slot row (`types/index.ts:1034`), i18n keys,
and vitest coverage.

**ASH:** GO — 7 scanners (bandit, checkov, detect-secrets, grype, npm-audit,
semgrep, syft), 0 attributable findings. cdk-nag/cfn-nag/opengrep MISSING (deps
not installed). Transitive dev-dependency npm advisories are pre-existing
`node_modules` noise — the diff adds no dependencies.

**Holmes:** not run for this frontend-only diff — no regex/secrets/IaC/data-handling
in the changed TS/JSON; the §12.6 backend already cleared Holmes last session
(1 pre-existing regex_dos in context.py, unrelated to this diff).

**Adversarial crew (6 axes — Correctness, Security, Tests, Docs-honesty,
AI-necessity, UX):** NO-GO → GO. Security GO (auth mirrors listCoordinationProjects,
no XSS sink — PR is plain text not an href, no secrets, harness loopback-only).
AI-necessity GO (fully deterministic fetch+JSX, zero LLM). Fixed the convergent
Highs: Correctness — `data.project.name` crash on a malformed 200 (defensive
optional chain); UX — Coordination offered to untagged sessions was a dead end
(withheld via effectiveHiddenViews, mirrors the Summary gate) and the members-row
title overflowed the narrow panel (flex-1 min-w-0). Fixed Mediums: empty-state
copy now names the action; collision participant list wraps. Nits: casing-robust
state pills, refresh busy state, no error+stale co-render. Tests strengthened
(section-presence floor, collision-key fallback, plural boundary, malformed-body
guard, error-keeps-data-out, withhold/appear coverage).

**Multimodel panel (3 rounds):** Opus PASS every round. GPT BLOCK r1→r2 on the same
whole-system-honesty axis (the header reported "Loading…" for a failed/malformed
completed load) — fixed by gating the header label on `isLoading` (loading only
while pending; a resolved-but-nameless or errored state shows `load_failed_short`).
r3 GO: GPT PASS, Opus PASS, First-Principles/Design/UX CONCERNS (non-blocking).

**Docs-honesty correction:** the commit prose "other 10 locales filled by the
ship-it i18n step" (897efb79) was inaccurate — the `coordinationPanel` keys exist
only in `en.manual.json` + `en-XA.json`. The other 10 real locales are NOT yet
translated; that is a documented pending step (ship-it i18n per-locale sweep),
recorded here rather than left as a false "filled" claim.

**Paired verdict @ `c49f22f8b`:**
crew NO-GO→GO (all Correctness/UX Highs + Mediums fixed) |
panel GO (GPT PASS, Opus PASS, First-Principles/Design/UX CONCERNS — non-blocking).
Both gates GO. Documented follow-ups (not this gate): loading skeleton, an inline
tag-CTA on the untagged empty state, the manually-synced ViewKind membership
tables, and the focus-return poll burst. The user merges.
